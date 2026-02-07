import unittest
import os

class TestHelloWorld(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.exists('hello_world.html'), 'hello_world.html file does not exist')

    def test_content(self):
        if not os.path.exists('hello_world.html'):
            self.skipTest('hello_world.html not created yet')
        with open('hello_world.html', 'r') as f:
            content = f.read()
            self.assertIn('<h1>Hello World</h1>', content, 'H1 tag with Hello World not found')
            self.assertIn('<title>Hello World</title>', content, 'Title tag with Hello World not found')

if __name__ == '__main__':
    unittest.main()