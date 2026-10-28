from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'rtm_watcher'

setup(
    name=package_name,
    version='0.2.0',
    packages=find_packages(exclude=['test']),
    package_data={package_name: ['demo_frames/*.jpg']},
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools', 'PyYAML'],
    zip_safe=True,
    maintainer='agog13',
    maintainer_email='arpit.wpi@gmail.com',
    description='VLM watcher that fires time-addressed triggers for ROS Time Machine',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'watcher_node = rtm_watcher.watcher_node:main',
            'fake_vlm = rtm_watcher.fake_vlm:main',
            'demo_sensors = rtm_watcher.demo_sensors:main',
            'effort_scorer = rtm_watcher.effort_scorer:main',
            'review = rtm_watcher.review:main',
        ],
    },
)
