cmake_minimum_required(VERSION 3.8)
project(ros_time_machine)

# Run the Python launch tests with the interpreter ROS itself uses, even when pyenv, conda
# or uv put another python3 first on PATH (same block as in ros_time_machine_msgs).
# Pass -DPython3_EXECUTABLE=... to choose one yourself.
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
find_package(rclcpp REQUIRED)
find_package(rosbag2_cpp REQUIRED)
find_package(rosbag2_storage REQUIRED)
find_package(std_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(yaml-cpp REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(ros_time_machine_msgs REQUIRED)

# ROS-free core: pins, time-addressed trigger queue, event.json sidecar
add_library(rtm_core
  src/pin_manager.cpp
  src/trigger_queue.cpp
  src/event_sidecar.cpp
)

target_include_directories(rtm_core PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(rtm_core PUBLIC cxx_std_17)

# Config Manager library
add_library(config_manager
  src/config_manager.cpp
)

target_include_directories(config_manager PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(config_manager PUBLIC cxx_std_17)

target_link_libraries(config_manager
  yaml-cpp
)

ament_target_dependencies(config_manager
  rclcpp
)

# Buffer Manager library
add_library(buffer_manager
  src/buffer_manager.cpp
)

target_include_directories(buffer_manager PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(buffer_manager PUBLIC cxx_std_17)

ament_target_dependencies(buffer_manager
  rclcpp
  rosbag2_cpp
  rosbag2_storage
)

# Trigger Monitor library
add_library(trigger_monitor
  src/trigger_monitor.cpp
)

target_include_directories(trigger_monitor PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(trigger_monitor PUBLIC cxx_std_17)

ament_target_dependencies(trigger_monitor
  rclcpp
  std_msgs
  sensor_msgs
)

# Recorder library
add_library(recorder
  src/recorder.cpp
)

target_include_directories(recorder PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(recorder PUBLIC cxx_std_17)

target_link_libraries(recorder
  rtm_core
  buffer_manager
)

ament_target_dependencies(recorder
  rclcpp
  rosbag2_cpp
  rosbag2_storage
)

# ROS interface library (EventTrigger, pin services, stats)
add_library(ros_interface
  src/ros_interface.cpp
)

target_include_directories(ros_interface PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(ros_interface PUBLIC cxx_std_17)

target_link_libraries(ros_interface
  recorder
  buffer_manager
)

ament_target_dependencies(ros_interface
  rclcpp
  rosbag2_cpp
  builtin_interfaces
  ros_time_machine_msgs
)

# Main executable
add_executable(time_machine_node src/time_machine_node.cpp)

target_include_directories(time_machine_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
)

target_compile_features(time_machine_node PUBLIC c_std_99 cxx_std_17)

target_link_libraries(time_machine_node
  config_manager
  buffer_manager
  trigger_monitor
  recorder
  ros_interface
)

ament_target_dependencies(time_machine_node
  rclcpp
  rosbag2_cpp
  std_msgs
  sensor_msgs
  ros_time_machine_msgs
)

# Install targets
install(TARGETS
  time_machine_node
  rtm_core
  config_manager
  buffer_manager
  trigger_monitor
  recorder
  ros_interface
  DESTINATION lib/${PROJECT_NAME}
)

# Install header files
install(DIRECTORY include/
  DESTINATION include/${PROJECT_NAME}
)

# Install launch files
install(DIRECTORY launch/
  DESTINATION share/${PROJECT_NAME}/launch
)

# Install config files
install(DIRECTORY config/
  DESTINATION share/${PROJECT_NAME}/config
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  find_package(ament_cmake_gtest REQUIRED)
  find_package(launch_testing_ament_cmake REQUIRED)

  # the following line skips the linter which checks for copyrights
  # comment the line when a copyright and license is added to all source files
  set(ament_cmake_copyright_FOUND TRUE)
  # the following line skips cpplint (only works in a git repo)
  # comment the line when this package is in a git repo and when
  # a copyright and license is added to all source files
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()

  # Unit tests - CircularBuffer
  ament_add_gtest(test_circular_buffer test/test_circular_buffer.cpp)
  target_include_directories(test_circular_buffer PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
  )
  target_compile_features(test_circular_buffer PUBLIC cxx_std_17)

  # Unit tests - ROS-free core (pins, trigger queue, sidecar)
  ament_add_gtest(test_pin_manager test/test_pin_manager.cpp)
  target_link_libraries(test_pin_manager rtm_core)
  ament_add_gtest(test_trigger_queue test/test_trigger_queue.cpp)
  target_link_libraries(test_trigger_queue rtm_core)
  ament_add_gtest(test_event_sidecar test/test_event_sidecar.cpp)
  target_link_libraries(test_event_sidecar rtm_core)

  # Unit tests - ConfigManager
  ament_add_gtest(test_config_manager test/test_config_manager.cpp)
  target_link_libraries(test_config_manager config_manager)
  target_include_directories(test_config_manager PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include/${PROJECT_NAME}>
  )
  target_compile_features(test_config_manager PUBLIC cxx_std_17)
  ament_target_dependencies(test_config_manager
    rclcpp
  )

  # Install test config files
  install(
    FILES
      test/test_config.yaml
      test/test_config_invalid.yaml
    DESTINATION share/${PROJECT_NAME}/test
  )

  # Integration test using launch_testing
  add_launch_test(
    test/test_integration.py
    TARGET test_integration
    TIMEOUT 30
  )

  # v2: time-addressed triggers, merging, pins, event.json
  add_launch_test(
    test/test_event_trigger_integration.py
    TARGET test_event_trigger_integration
    TIMEOUT 90
  )
endif()

ament_package()
