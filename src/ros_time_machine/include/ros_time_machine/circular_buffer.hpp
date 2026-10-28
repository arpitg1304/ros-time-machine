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
#include <deque>
#include <mutex>
#include <memory>
#include <chrono>
#include <functional>

namespace ros_time_machine
{

/**
 * @brief A thread-safe circular buffer with time-based retrieval
 *
 * This circular buffer stores elements with timestamps and allows
 * retrieval of elements within a time range. When the buffer is full,
 * the oldest element is overwritten.
 *
 * An optional eviction guard protects time ranges from being lost: an element
 * that would be overwritten while the guard covers its timestamp is moved to a
 * bounded "retained" store instead, and stays readable through getRange() until
 * pruneRetained() finds it is no longer covered. This is how pinned ranges and
 * pending time-addressed triggers survive a small ring.
 *
 * @tparam T Type of elements to store
 */
template<typename T>
class CircularBuffer
{
public:
  using TimePoint = std::chrono::system_clock::time_point;
  using Duration = std::chrono::duration<double>;
  using EvictionGuard = std::function<bool (const TimePoint &)>;

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
      dropped_count_(0),
      max_retained_(capacity / 4),
      retained_dropped_count_(0)
  {
  }

  /**
   * @brief Protect elements from eviction while guard(timestamp) returns true
   *
   * The guard is called with the buffer lock held, so it must not call back
   * into this buffer.
   *
   * @param guard Predicate over element timestamps; empty to disable
   * @param max_retained Most elements held past eviction (the pin budget)
   */
  void setEvictionGuard(EvictionGuard guard, size_t max_retained)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    guard_ = std::move(guard);
    max_retained_ = max_retained;
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
      retainOrDrop(buffer_[tail_]);
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

    // Retained elements are always older than anything still in the ring
    for (const auto & elem : retained_) {
      if (elem.timestamp >= start_time && elem.timestamp <= end_time) {
        result.push_back(elem);
      }
    }

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
    return getRecent(duration, std::chrono::system_clock::now());
  }

  /**
   * @brief Get all elements newer than a duration before an explicit "now"
   *
   * Use this overload when timestamps come from a ROS clock (sim time, bag
   * replay), where wall-clock now() is meaningless.
   */
  std::vector<TimestampedElement> getRecent(const Duration & duration, const TimePoint & now) const
  {
    auto start_time_duration = now - duration;
    // Cast to TimePoint duration type for consistency
    auto start_time = std::chrono::time_point_cast<TimePoint::duration>(start_time_duration);
    return getRange(start_time, now);
  }

  /**
   * @brief Drop retained elements the eviction guard no longer covers
   *
   * @return size_t Number of elements released
   */
  size_t pruneRetained()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t before = retained_.size();
    std::deque<TimestampedElement> kept;
    for (auto & elem : retained_) {
      if (guard_ && guard_(elem.timestamp)) {
        kept.push_back(std::move(elem));
      }
    }
    retained_.swap(kept);
    return before - retained_.size();
  }

  /**
   * @brief Number of elements held past eviction because they were protected
   */
  size_t retainedCount() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return retained_.size();
  }

  /**
   * @brief Number of protected elements dropped because the retained store was full
   */
  uint64_t retainedDroppedCount() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return retained_dropped_count_;
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
    if (!retained_.empty()) {
      return retained_.front().timestamp;
    }
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
    retained_.clear();
  }

private:
  // Called with mutex_ held for the element about to be overwritten
  void retainOrDrop(TimestampedElement & elem)
  {
    if (!guard_ || max_retained_ == 0 || !guard_(elem.timestamp)) {
      dropped_count_++;
      return;
    }
    if (retained_.size() >= max_retained_) {
      // Pin budget exhausted: the oldest protected element goes first
      retained_.pop_front();
      retained_dropped_count_++;
      dropped_count_++;
    }
    retained_.push_back(std::move(elem));
  }

  const size_t capacity_;
  std::vector<TimestampedElement> buffer_;
  size_t head_;  // Index where next element will be written
  size_t tail_;  // Index of oldest element
  size_t size_;  // Current number of elements
  uint64_t dropped_count_;  // Number of messages dropped due to overflow
  EvictionGuard guard_;     // Protects pinned timestamps from eviction
  std::deque<TimestampedElement> retained_;  // Protected elements evicted from the ring
  size_t max_retained_;     // Retained store limit (pin budget)
  uint64_t retained_dropped_count_;  // Protected elements lost to the pin budget
  mutable std::mutex mutex_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__CIRCULAR_BUFFER_HPP_
