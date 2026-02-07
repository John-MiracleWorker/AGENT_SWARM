import unittest
from server.core.task_manager import TaskManager, TaskStatus

class TestTaskManager(unittest.TestCase):
    def setUp(self):
        self.tm = TaskManager()

    def test_create_task(self):
        task = self.tm.create_task(title="Test Task", description="A test task", created_by="tester")
        self.assertIsNotNone(task.id)
        self.assertEqual(task.title, "Test Task")
        self.assertEqual(task.status, TaskStatus.TODO)

    def test_update_status(self):
        task = self.tm.create_task(title="Task 2", description="Desc", created_by="tester")
        updated = self.tm.update_status(task.id, TaskStatus.IN_PROGRESS)
        self.assertEqual(updated.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(self.tm.get_task(task.id).status, TaskStatus.IN_PROGRESS)

    def test_assign_task(self):
        task = self.tm.create_task(title="Task 3", description="Desc", created_by="tester")
        self.tm.assign_task(task.id, "developer")
        self.assertEqual(self.tm.get_task(task.id).assignee, "developer")

    def test_get_summary(self):
        self.tm.create_task("T1", "D1", "tester")
        self.tm.create_task("T2", "D2", "tester")
        summary = self.tm.get_summary()
        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['todo'], 2)

if __name__ == '__main__':
    unittest.main()