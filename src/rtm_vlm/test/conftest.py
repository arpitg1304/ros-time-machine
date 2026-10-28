import os
import sys

# Run the ROS-free tests with plain pytest from a source checkout
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..', '..', 'rtm_watcher')))
