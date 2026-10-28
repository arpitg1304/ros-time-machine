from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'rtm_vlm'

setup(
    name=package_name,
    version='0.2.0',
    packages=find_packages(exclude=['test']),
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
    description='Local VLM deployment and benchmarking for ROS Time Machine semantic triggers',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'vlm = rtm_vlm.cli:main',
        ],
    },
)
