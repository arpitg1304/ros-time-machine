# Copyright 2024 ROS Time Machine Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A tiny bundled camera frame for demos and benchmarks (no ROS needed)."""

import base64

# 96x64 JPEG of a red cup on a table
DEMO_JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIf'
    'IiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7'
    'Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCABAAGADASIA'
    'AhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQA'
    'AAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3'
    'ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWm'
    'p6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA'
    'AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx'
    'BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElK'
    'U1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3'
    'uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzyiii'
    'tCQooooAKKKs/wBn3X/PL/x4f41Lko7s0hSqVPgi36K5Woqz/Z91/wA8v/Hh/jVdlKMVYYKnBFCl'
    'F7MJ0alPWcWvVCUUUVRmFFFFABRRVuDSr25hWaGDcjdDuUe3rQBUoq//AGHqX/Pt/wCPr/jR/Yep'
    'f8+3/j6/40AUK6Osv+w9S/59v/H1/wAa1/Il/wCeT/8AfJrjxSbtY9/JZRj7Tmdtv1GVgXP/AB9S'
    '/wC+f510XkS/88n/AO+TWXNo2oSTO62+VZiR86jjP1qcKmm7mmczjKnHld9TNoq//Yepf8+3/j6/'
    '40f2HqX/AD7f+Pr/AI13HzhQoq7Jo2oRRtI9vhUBYnevAH41SoAK67Q/+QPB/wAC/wDQjXI1NHd3'
    'MSBI7iVFHRVcgCgDt6K4n7fe/wDP3P8A9/DR9vvf+fuf/v4aAudtRXE/b73/AJ+5/wDv4aPt97/z'
    '9z/9/DQFztqK4n7fe/8AP3P/AN/DR9vvf+fuf/v4aAudtRXE/b73/n7n/wC/ho+33v8Az9z/APfw'
    '0Bc6+/8A+Qdc/wDXF/5GuJqdr27dSrXUzKRggyEgioKACiiigAooooAKKKKACiiigAooooAKKKKA'
    'P//Z'
)
