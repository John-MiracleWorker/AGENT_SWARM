import subprocess
import os

def test_hello_swarm_output():
    assert os.path.exists("hello_swarm.py"), "hello_swarm.py should exist"
    result = subprocess.run(["python", "hello_swarm.py"], capture_output=True, text=True)
    assert result.returncode == 0, "Script should run successfully"
    assert "Hello, Agent Swarm!" in result.stdout.strip(), "Output should match expected text"
