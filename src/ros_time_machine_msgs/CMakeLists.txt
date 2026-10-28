cmake_minimum_required(VERSION 3.8)
project(ros_time_machine_msgs)

# Build the Python bindings for the interpreter ROS itself uses, even when pyenv, conda or
# uv put another python3 first on PATH. Bindings for the wrong version build cleanly but
# fail to import at runtime. Pass -DPython3_EXECUTABLE=... to choose one yourself.
if(NOT DEFINED Python3_EXECUTABLE)
  string(REPLACE ":" ";" _rtm_prefixes "$ENV{AMENT_PREFIX_PATH}")
  foreach(_rtm_prefix IN LISTS _rtm_prefixes)
    file(GLOB _rtm_rclpy "${_rtm_prefix}/lib/python3.*/site-packages/rclpy")
    if(_rtm_rclpy)
      list(GET _rtm_rclpy 0 _rtm_rclpy)
      string(REGEX MATCH "python3\\.[0-9]+" _rtm_python_name "${_rtm_rclpy}")
      # RoboStack/conda installs ROS into the environment root; apt installs use /usr/bin
      find_program(_rtm_python NAMES ${_rtm_python_name}
        PATHS "${_rtm_prefix}/bin" /usr/bin /usr/local/bin NO_DEFAULT_PATH)
      if(_rtm_python)
        set(Python3_EXECUTABLE "${_rtm_python}" CACHE FILEPATH "Python used by ROS")
        message(STATUS "Using ROS's Python: ${_rtm_python}")
      endif()
      break()
    endif()
  endforeach()
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)

# Generate messages and services
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/RecordingStatus.msg"
  "msg/BufferStats.msg"
  "msg/EventTrigger.msg"
  "srv/TriggerSnapshot.srv"
  "srv/GetStatus.srv"
  "srv/ListSnapshots.srv"
  "srv/PinRange.srv"
  "srv/ReleasePin.srv"
  DEPENDENCIES std_msgs builtin_interfaces
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
