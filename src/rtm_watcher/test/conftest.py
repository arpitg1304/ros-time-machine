import os
import sys

# Allow running the ROS-free tests with plain pytest from a source checkout
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
