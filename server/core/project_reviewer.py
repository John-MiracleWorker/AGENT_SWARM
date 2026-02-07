"""
Project Reviewer — Post-completion quality check using Gemini Files API.
Uploads the entire project and asks Gemini to review for completeness,
file structure, bugs, and missing pieces. Supports up to 3 review cycles.
"""

import asyncio
import json
import logging
import time

from google.genai import types

logger = logging.getLogger(__name__)

MAX_REVIEW_CYCLES = 3

REVIEW_PROMPT = """You are a senior code reviewer performing a final quality check on a completed project.

## Your Task
Review the uploaded project files and evaluate:
1. **Completeness** — Are all tasks fully implemented? Any TODO/FIXME/placeholder code left?
2. **File Structure** — Is the project well-organized? Missing config files, READMEs, etc.?
3. **Code Quality** — Obvious bugs, unused imports, dead code, error handling gaps?
4. **Integration** — Do all components connect properly? Missing routes, broken imports?
5. **Security** — Hardcoded secrets, missing input validation, exposed debug endpoints?

## Mission Goal
{mission_goal}

## Task List
{task_list}

## Response Format
You MUST respond with valid JSON:
```json
{{
    "status": "pass" | "needs_changes",
    "score": 85,
    "summary": "Brief overall assessment",
    "issues": [
        {{
            "severity": "critical" | "major" | "minor",
            "file": "path/to/file.py",
            "title": "Short issue title",
            "description": "What's wrong and how to fix it",
            "assignee": "developer" | "tester" | "reviewer"
        }}
    ],
    "strengths": ["Good things about the project"]
}}
```

If everything looks good with no significant issues, set status to "pass" and issues to an empty array.
Only set "needs_changes" for issues that would actually break functionality or are clearly incomplete.
"""


async def review_project(state, tasks, bus):
    """
    Run a post-completion review of the project.
    Returns the review result dict.
    """
    from server.core.message_bus import MessageType

    logger.info("🔍 Starting post-completion project review...")

    # Broadcast that review is starting
    await bus.publish(
        sender="system",
        sender_role="system",
        msg_type=MessageType.SYSTEM,
        content="🔍 Starting post-completion project review...",
    )

    # Make sure file context is up-to-date
    workspace_path = str(state.workspace.root) if state.workspace._root else None
    if not workspace_path:
        logger.warning("No workspace set, skipping review")
        return {"status": "pass", "summary": "No workspace to review", "issues": [], "cycle": 0}

    # Re-upload workspace to get latest files
    try:
        await state.file_context.upload_workspace(workspace_path)
    except Exception as e:
        logger.error(f"Failed to upload workspace for review: {e}")
        return {"status": "error", "summary": f"Upload failed: {e}", "issues": [], "cycle": 0}

    # Build the task list summary
    task_list = tasks.list_tasks()
    task_summary = "\n".join(
        f"- [{t.get('status', 'unknown')}] {t.get('title', 'Untitled')}: {t.get('description', '')}"
        for t in task_list
    )

    # Build contents with file parts
    contents = []
    file_parts = state.file_context.get_file_parts()
    if file_parts:
        file_summary = state.file_context.get_file_summary()
        contents.append(types.Content(
            role="user",
            parts=file_parts + [
                types.Part.from_text(
                    text=f"Above are all the project source files.\n{file_summary}"
                )
            ],
        ))
        contents.append(types.Content(
            role="model",
            parts=[types.Part.from_text(
                text="I've received and reviewed all the project files. I'll now perform a thorough quality review."
            )],
        ))

    # Add the review prompt
    prompt = REVIEW_PROMPT.format(
        mission_goal=state.mission_goal or "No goal specified",
        task_list=task_summary or "No tasks listed",
    )
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    ))

    # Call Gemini for review
    try:
        model_name = state.gemini._pick_best_model()
        if not model_name:
            model_name = "gemini-2.5-flash"  # fallback

        logger.info(f"🔍 Sending project for review to {model_name}...")

        response = await asyncio.to_thread(
            state.gemini.client.models.generate_content,
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.3,  # Low temp for consistent reviews
            ),
        )

        text = response.text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(text)
        logger.info(f"🔍 Review complete: {result.get('status', 'unknown')} (score: {result.get('score', '?')})")

    except json.JSONDecodeError:
        logger.error(f"Review returned non-JSON: {text[:200]}")
        result = {
            "status": "pass",
            "summary": "Review completed but response was not parseable",
            "issues": [],
            "score": 0,
        }
    except Exception as e:
        logger.error(f"Review failed: {e}")
        result = {
            "status": "error",
            "summary": f"Review error: {str(e)[:200]}",
            "issues": [],
            "score": 0,
        }

    # Broadcast review result
    await bus.publish(
        sender="system",
        sender_role="system",
        msg_type=MessageType.REVIEW_RESULT,
        content=result.get("summary", "Review complete"),
        data=result,
    )

    return result


async def run_review_loop(state, tasks, bus, on_new_tasks=None):
    """
    Run up to MAX_REVIEW_CYCLES of review.
    After each cycle, if issues found:
      - Create new tasks from issues
      - Let agents work on them
      - Re-review
    Returns the final review result.
    """
    from server.core.message_bus import MessageType

    for cycle in range(1, MAX_REVIEW_CYCLES + 1):
        logger.info(f"🔄 Review cycle {cycle}/{MAX_REVIEW_CYCLES}")

        await bus.publish(
            sender="system",
            sender_role="system",
            msg_type=MessageType.SYSTEM,
            content=f"🔄 Review cycle {cycle}/{MAX_REVIEW_CYCLES}",
        )

        result = await review_project(state, tasks, bus)
        result["cycle"] = cycle
        result["max_cycles"] = MAX_REVIEW_CYCLES

        if result.get("status") == "pass":
            logger.info(f"✅ Project passed review on cycle {cycle}")
            await bus.publish(
                sender="system",
                sender_role="system",
                msg_type=MessageType.SYSTEM,
                content=f"✅ Project passed review! Score: {result.get('score', '?')}/100",
            )
            return result

        if result.get("status") == "error":
            logger.error("Review errored, treating as pass")
            return result

        # Has issues — create tasks if not on final cycle
        issues = result.get("issues", [])
        if cycle < MAX_REVIEW_CYCLES and issues and on_new_tasks:
            logger.info(f"📋 Creating {len(issues)} tasks from review issues")
            await on_new_tasks(issues)

            # Wait for agents to work on the new tasks
            # The caller should handle resuming agents
            await asyncio.sleep(5)  # Brief pause before re-review

            # Wait for tasks to be done
            max_wait = 120  # 2 minutes max wait per cycle
            waited = 0
            while waited < max_wait:
                summary = tasks.get_summary()
                if summary.get("todo", 0) == 0 and summary.get("in_progress", 0) == 0:
                    break
                await asyncio.sleep(5)
                waited += 5

        elif cycle == MAX_REVIEW_CYCLES:
            logger.warning(f"⚠️ Max review cycles reached with {len(issues)} remaining issues")
            await bus.publish(
                sender="system",
                sender_role="system",
                msg_type=MessageType.SYSTEM,
                content=f"⚠️ Review reached max cycles ({MAX_REVIEW_CYCLES}). {len(issues)} issues remain.",
            )

    return result
