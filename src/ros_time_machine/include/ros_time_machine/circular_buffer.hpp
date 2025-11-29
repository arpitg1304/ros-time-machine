// Copyright 2024 ROS Time Machine Contributors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef ROS_TIME_MACHINE__CIRCULAR_BUFFER_HPP_
#define ROS_TIME_MACHINE__CIRCULAR_BUFFER_HPP_

#include <vector>
#include <mutex>
#include <memory>
#include <chrono>

namespace ros_time_machine
{

/**
 * @brief A thread-safe circular buffer with time-based retrieval
 *
 * This circular buffer stores elements with timestamps and allows
 * retrieval of elements within a time range. When the buffer is full,
 * the oldest element is overwritten.
 *
 * @tparam T Type of elements to store
 */
template<typename T>
class CircularBuffer
{
public:
  using TimePoint = std::chrono::system_clock::time_point;
  using Duration = std::chrono::duration<double>;

  /**
   * @brief Struct to hold an element with its timestamp
   */
  struct TimestampedElement
  {
    TimePoint timestamp;
    T data;

    // Default constructor
    TimestampedElement() : timestamp(), data() {}

    // Parameterized constructor
    TimestampedElement(const TimePoint & ts, const T & d)
      : timestamp(ts), data(d) {}
  };

  /**
   * @brief Construct a new Circular Buffer
   *
   * @param capacity Maximum number of elements to store
   */
  explicit CircularBuffer(size_t capacity)
    : capacity_(capacity),
      buffer_(capacity),
      head_(0),
      tail_(0),
      size_(0),
      dropped_count_(0)
  {
  }

  /**
   * @brief Add an element to the buffer
   *
   * @param timestamp Timestamp of the element
   * @param data The data to store
   * @return true if element was added, false if it overwrote an old element
   */
  bool push(const TimePoint & timestamp, const T & data)
  {
    std::lock_guard<std::mutex> lock(mutex_);

    bool overwritten = (size_ == capacity_);
    if (overwritten) {
      dropped_count_++;
      tail_ = (tail_ + 1) % capacity_;
    }

    buffer_[head_] = TimestampedElement(timestamp, data);
    head_ = (head_ + 1) % capacity_;

    if (!overwritten) {
      size_++;
    }

    return !overwritten;
  }

  /**
   * @brief Get all elements within a time range
   *
   * @param start_time Start of the time range
   * @param end_time End of the time range
   * @return std::vector<TimestampedElement> Elements within the range
   */
  std::vector<TimestampedElement> getRange(
    const TimePoint & start_time,
    const TimePoint & end_time) const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<TimestampedElement> result;

    if (size_ == 0) {
      return result;
    }

    size_t current = tail_;
    for (size_t i = 0; i < size_; ++i) {
      const auto & elem = buffer_[current];
      if (elem.timestamp >= start_time && elem.timestamp <= end_time) {
        result.push_back(elem);
      }
      current = (current + 1) % capacity_;
    }

    return result;
  }

  /**
   * @brief Get all elements newer than a given duration
   *
   * @param duration How far back to retrieve (e.g., last 5 seconds)
   * @return std::vector<TimestampedElement> Recent elements
   */
  std::vector<TimestampedElement> getRecent(const Duration & duration) const
  {
    auto now = std::chrono::system_clock::now();
    auto start_time_duration = now - duration;
    // Cast to TimePoint duration type for consistency
    auto start_time = std::chrono::time_point_cast<TimePoint::duration>(start_time_duration);
    return getRange(start_time, now);
  }

  /**
   * @brief Get the current size of the buffer
   *
   * @return size_t Number of elements currently stored
   */
  size_t size() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return size_;
  }

  /**
   * @brief Get the capacity of the buffer
   *
   * @return size_t Maximum number of elements
   */
  size_t capacity() const
  {
    return capacity_;
  }

  /**
   * @brief Check if the buffer is empty
   *
   * @return true if empty
   */
  bool empty() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return size_ == 0;
  }

  /**
   * @brief Check if the buffer is full
   *
   * @return true if full
   */
  bool full() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return size_ == capacity_;
  }

  /**
   * @brief Get the number of dropped messages (overwritten)
   *
   * @return uint64_t Count of dropped messages
   */
  uint64_t droppedCount() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return dropped_count_;
  }

  /**
   * @brief Get the timestamp of the oldest element
   *
   * @return TimePoint Timestamp of oldest element, or epoch if empty
   */
  TimePoint oldestTimestamp() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (size_ == 0) {
      return TimePoint();
    }
    return buffer_[tail_].timestamp;
  }

  /**
   * @brief Get the timestamp of the newest element
   *
   * @return TimePoint Timestamp of newest element, or epoch if empty
   */
  TimePoint newestTimestamp() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (size_ == 0) {
      return TimePoint();
    }
    size_t newest_idx = (head_ == 0) ? capacity_ - 1 : head_ - 1;
    return buffer_[newest_idx].timestamp;
  }

  /**
   * @brief Clear all elements from the buffer
   */
  void clear()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    head_ = 0;
    tail_ = 0;
    size_ = 0;
  }

private:
  const size_t capacity_;
  std::vector<TimestampedElement> buffer_;
  size_t head_;  // Index where next element will be written
  size_t tail_;  // Index of oldest element
  size_t size_;  // Current number of elements
  uint64_t dropped_count_;  // Number of messages dropped due to overflow
  mutable std::mutex mutex_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__CIRCULAR_BUFFER_HPP_
