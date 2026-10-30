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

#include <gtest/gtest.h>
#include <thread>
#include <chrono>
#include <vector>
#include "ros_time_machine/circular_buffer.hpp"

using namespace ros_time_machine;
using namespace std::chrono;

// Test fixture for CircularBuffer tests
class CircularBufferTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    // Create a buffer with capacity 5 for most tests
    buffer_ = std::make_unique<CircularBuffer<int>>(5);
  }

  void TearDown() override
  {
    buffer_.reset();
  }

  // Helper function to create a timestamp offset from now
  CircularBuffer<int>::TimePoint makeTimestamp(double offset_seconds)
  {
    auto now = system_clock::now();
    auto offset = duration_cast<system_clock::duration>(
      duration<double>(offset_seconds));
    return now + offset;
  }

  std::unique_ptr<CircularBuffer<int>> buffer_;
};

// Basic functionality tests
TEST_F(CircularBufferTest, InitialStateIsEmpty)
{
  EXPECT_TRUE(buffer_->empty());
  EXPECT_FALSE(buffer_->full());
  EXPECT_EQ(buffer_->size(), 0u);
  EXPECT_EQ(buffer_->capacity(), 5u);
  EXPECT_EQ(buffer_->droppedCount(), 0u);
}

TEST_F(CircularBufferTest, PushSingleElement)
{
  auto ts = system_clock::now();
  bool result = buffer_->push(ts, 42);

  EXPECT_TRUE(result);  // No overwrite
  EXPECT_EQ(buffer_->size(), 1u);
  EXPECT_FALSE(buffer_->empty());
  EXPECT_FALSE(buffer_->full());
  EXPECT_EQ(buffer_->droppedCount(), 0u);
}

TEST_F(CircularBufferTest, PushUntilFull)
{
  auto ts = system_clock::now();

  // Push 5 elements (capacity is 5)
  for (int i = 0; i < 5; ++i) {
    bool result = buffer_->push(ts, i);
    EXPECT_TRUE(result);  // Should not overwrite
  }

  EXPECT_TRUE(buffer_->full());
  EXPECT_EQ(buffer_->size(), 5u);
  EXPECT_EQ(buffer_->droppedCount(), 0u);
}

TEST_F(CircularBufferTest, OverwriteOldestWhenFull)
{
  auto ts = system_clock::now();

  // Fill buffer
  for (int i = 0; i < 5; ++i) {
    buffer_->push(ts, i);
  }

  // Push one more - should overwrite oldest
  bool result = buffer_->push(ts, 99);

  EXPECT_FALSE(result);  // Indicates overwrite
  EXPECT_EQ(buffer_->size(), 5u);  // Size stays at capacity
  EXPECT_TRUE(buffer_->full());
  EXPECT_EQ(buffer_->droppedCount(), 1u);
}

TEST_F(CircularBufferTest, MultipleOverwrites)
{
  auto ts = system_clock::now();

  // Fill buffer and overwrite 3 times
  for (int i = 0; i < 8; ++i) {
    buffer_->push(ts, i);
  }

  EXPECT_EQ(buffer_->size(), 5u);
  EXPECT_EQ(buffer_->droppedCount(), 3u);
}

TEST_F(CircularBufferTest, ClearBuffer)
{
  auto ts = system_clock::now();

  // Add some elements
  for (int i = 0; i < 3; ++i) {
    buffer_->push(ts, i);
  }

  buffer_->clear();

  EXPECT_TRUE(buffer_->empty());
  EXPECT_EQ(buffer_->size(), 0u);
  EXPECT_FALSE(buffer_->full());
}

// Timestamp functionality tests
TEST_F(CircularBufferTest, OldestAndNewestTimestamps)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);
  auto t3 = makeTimestamp(2.0);

  buffer_->push(t1, 1);
  buffer_->push(t2, 2);
  buffer_->push(t3, 3);

  EXPECT_EQ(buffer_->oldestTimestamp(), t1);
  EXPECT_EQ(buffer_->newestTimestamp(), t3);
}

TEST_F(CircularBufferTest, TimestampsAfterOverwrite)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);
  auto t3 = makeTimestamp(2.0);
  auto t4 = makeTimestamp(3.0);
  auto t5 = makeTimestamp(4.0);
  auto t6 = makeTimestamp(5.0);

  // Fill buffer with t1-t5
  buffer_->push(t1, 1);
  buffer_->push(t2, 2);
  buffer_->push(t3, 3);
  buffer_->push(t4, 4);
  buffer_->push(t5, 5);

  // Overwrite oldest (t1) with t6
  buffer_->push(t6, 6);

  EXPECT_EQ(buffer_->oldestTimestamp(), t2);  // t1 was dropped
  EXPECT_EQ(buffer_->newestTimestamp(), t6);
}

TEST_F(CircularBufferTest, EmptyBufferTimestamps)
{
  auto epoch = CircularBuffer<int>::TimePoint();
  EXPECT_EQ(buffer_->oldestTimestamp(), epoch);
  EXPECT_EQ(buffer_->newestTimestamp(), epoch);
}

// Range retrieval tests
TEST_F(CircularBufferTest, GetRangeAllElements)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);
  auto t3 = makeTimestamp(2.0);

  buffer_->push(t1, 10);
  buffer_->push(t2, 20);
  buffer_->push(t3, 30);

  // Get all elements
  auto result = buffer_->getRange(t1, t3);

  ASSERT_EQ(result.size(), 3u);
  EXPECT_EQ(result[0].data, 10);
  EXPECT_EQ(result[1].data, 20);
  EXPECT_EQ(result[2].data, 30);
}

TEST_F(CircularBufferTest, GetRangePartialElements)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);
  auto t3 = makeTimestamp(2.0);
  auto t4 = makeTimestamp(3.0);

  buffer_->push(t1, 10);
  buffer_->push(t2, 20);
  buffer_->push(t3, 30);
  buffer_->push(t4, 40);

  // Get elements between t2 and t3
  auto result = buffer_->getRange(t2, t3);

  ASSERT_EQ(result.size(), 2u);
  EXPECT_EQ(result[0].data, 20);
  EXPECT_EQ(result[1].data, 30);
}

TEST_F(CircularBufferTest, GetRangeNoElements)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);
  auto t3 = makeTimestamp(2.0);

  buffer_->push(t1, 10);

  // Query range that doesn't include any elements
  auto result = buffer_->getRange(t2, t3);

  EXPECT_TRUE(result.empty());
}

TEST_F(CircularBufferTest, GetRangeFromEmptyBuffer)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);

  auto result = buffer_->getRange(t1, t2);

  EXPECT_TRUE(result.empty());
}

TEST_F(CircularBufferTest, GetRangeAfterWrapAround)
{
  auto t1 = makeTimestamp(0.0);
  auto t2 = makeTimestamp(1.0);
  auto t3 = makeTimestamp(2.0);
  auto t4 = makeTimestamp(3.0);
  auto t5 = makeTimestamp(4.0);
  auto t6 = makeTimestamp(5.0);
  auto t7 = makeTimestamp(6.0);

  // Fill buffer and cause wrap-around
  buffer_->push(t1, 1);
  buffer_->push(t2, 2);
  buffer_->push(t3, 3);
  buffer_->push(t4, 4);
  buffer_->push(t5, 5);
  buffer_->push(t6, 6);  // Overwrites t1
  buffer_->push(t7, 7);  // Overwrites t2

  // Buffer now contains: 3, 4, 5, 6, 7
  auto result = buffer_->getRange(t3, t7);

  ASSERT_EQ(result.size(), 5u);
  EXPECT_EQ(result[0].data, 3);
  EXPECT_EQ(result[1].data, 4);
  EXPECT_EQ(result[2].data, 5);
  EXPECT_EQ(result[3].data, 6);
  EXPECT_EQ(result[4].data, 7);
}

// Recent retrieval tests
TEST_F(CircularBufferTest, GetRecentElements)
{
  auto now = system_clock::now();
  auto t1 = now - seconds(5);
  auto t2 = now - seconds(3);
  auto t3 = now - seconds(1);

  buffer_->push(t1, 10);
  buffer_->push(t2, 20);
  buffer_->push(t3, 30);

  // Get elements from last 4 seconds
  auto result = buffer_->getRecent(duration<double>(4.0));

  // Should get t2 and t3 (within 4 seconds)
  EXPECT_GE(result.size(), 2u);
}

// Thread safety tests
TEST_F(CircularBufferTest, ConcurrentPushes)
{
  const int num_threads = 4;
  const int pushes_per_thread = 100;
  std::vector<std::thread> threads;

  for (int i = 0; i < num_threads; ++i) {
    threads.emplace_back([this, i, pushes_per_thread]() {
        for (int j = 0; j < pushes_per_thread; ++j) {
          auto ts = system_clock::now();
          buffer_->push(ts, i * pushes_per_thread + j);
        }
    });
  }

  for (auto & thread : threads) {
    thread.join();
  }

  // Buffer should be full (capacity 5)
  EXPECT_EQ(buffer_->size(), 5u);
  EXPECT_TRUE(buffer_->full());

  // Should have dropped many elements
  EXPECT_GT(buffer_->droppedCount(), 0u);
}

TEST_F(CircularBufferTest, ConcurrentPushAndRead)
{
  std::atomic<bool> stop{false};
  std::vector<std::thread> threads;

  // Writer thread
  threads.emplace_back([this, &stop]() {
      int counter = 0;
      while (!stop) {
        auto ts = system_clock::now();
        buffer_->push(ts, counter++);
        std::this_thread::sleep_for(milliseconds(1));
      }
  });

  // Reader threads
  for (int i = 0; i < 3; ++i) {
    threads.emplace_back([this, &stop]() {
        while (!stop) {
          auto size = buffer_->size();
          auto empty = buffer_->empty();
          auto full = buffer_->full();
          auto dropped = buffer_->droppedCount();
          (void)size; (void)empty; (void)full; (void)dropped;
          std::this_thread::sleep_for(milliseconds(5));
        }
    });
  }

  // Let threads run for a bit
  std::this_thread::sleep_for(milliseconds(100));
  stop = true;

  for (auto & thread : threads) {
    thread.join();
  }

  // Should not crash - that's the main test for thread safety
  SUCCEED();
}

// Edge cases
TEST_F(CircularBufferTest, SingleElementCapacity)
{
  auto single_buffer = CircularBuffer<int>(1);
  auto ts = system_clock::now();

  single_buffer.push(ts, 1);
  EXPECT_TRUE(single_buffer.full());
  EXPECT_EQ(single_buffer.size(), 1u);

  single_buffer.push(ts, 2);
  EXPECT_EQ(single_buffer.size(), 1u);
  EXPECT_EQ(single_buffer.droppedCount(), 1u);
}

TEST_F(CircularBufferTest, LargeCapacity)
{
  auto large_buffer = CircularBuffer<int>(10000);
  auto ts = system_clock::now();

  for (int i = 0; i < 5000; ++i) {
    large_buffer.push(ts, i);
  }

  EXPECT_EQ(large_buffer.size(), 5000u);
  EXPECT_FALSE(large_buffer.full());
  EXPECT_EQ(large_buffer.droppedCount(), 0u);
}

// Test with different data types
TEST(CircularBufferTypesTest, StringType)
{
  CircularBuffer<std::string> string_buffer(3);
  auto ts = system_clock::now();

  string_buffer.push(ts, "hello");
  string_buffer.push(ts, "world");
  string_buffer.push(ts, "test");

  EXPECT_EQ(string_buffer.size(), 3u);

  auto result = string_buffer.getRange(
    ts - seconds(1),
    ts + seconds(1)
  );

  ASSERT_EQ(result.size(), 3u);
  EXPECT_EQ(result[0].data, "hello");
  EXPECT_EQ(result[1].data, "world");
  EXPECT_EQ(result[2].data, "test");
}

TEST(CircularBufferTypesTest, StructType)
{
  struct TestData
  {
    int id;
    double value;
    bool operator==(const TestData & other) const
    {
      return id == other.id && value == other.value;
    }
  };

  CircularBuffer<TestData> struct_buffer(5);
  auto ts = system_clock::now();

  TestData d1{1, 1.5};
  TestData d2{2, 2.5};

  struct_buffer.push(ts, d1);
  struct_buffer.push(ts, d2);

  EXPECT_EQ(struct_buffer.size(), 2u);

  auto result = struct_buffer.getRange(
    ts - seconds(1),
    ts + seconds(1)
  );

  ASSERT_EQ(result.size(), 2u);
  EXPECT_TRUE(result[0].data == d1);
  EXPECT_TRUE(result[1].data == d2);
}

// Eviction guard (pins) tests
class PinnedBufferTest : public ::testing::Test
{
protected:
  using Buffer = CircularBuffer<int>;

  // Element i is stamped i seconds after a fixed epoch
  static Buffer::TimePoint at(int seconds_after_epoch)
  {
    return Buffer::TimePoint(seconds(seconds_after_epoch));
  }

  void fill(Buffer & buffer, int first, int last)
  {
    for (int i = first; i <= last; ++i) {
      buffer.push(at(i), i);
    }
  }
};

TEST_F(PinnedBufferTest, NoGuardBehavesLikeV1)
{
  Buffer buffer(5);
  fill(buffer, 0, 9);
  EXPECT_EQ(buffer.size(), 5u);
  EXPECT_EQ(buffer.retainedCount(), 0u);
  EXPECT_EQ(buffer.droppedCount(), 5u);
  EXPECT_EQ(buffer.oldestTimestamp(), at(5));
}

TEST_F(PinnedBufferTest, GuardedElementsSurviveEviction)
{
  Buffer buffer(5);
  buffer.setEvictionGuard(
    [](const Buffer::TimePoint & t) {return t >= at(1) && t <= at(3);}, 10);
  fill(buffer, 0, 9);

  EXPECT_EQ(buffer.size(), 5u);
  EXPECT_EQ(buffer.retainedCount(), 3u);
  EXPECT_EQ(buffer.droppedCount(), 2u);  // 0 and 4 were not protected
  EXPECT_EQ(buffer.oldestTimestamp(), at(1));

  auto result = buffer.getRange(at(0), at(9));
  ASSERT_EQ(result.size(), 8u);
  std::vector<int> values;
  for (const auto & elem : result) {
    values.push_back(elem.data);
  }
  EXPECT_EQ(values, (std::vector<int>{1, 2, 3, 5, 6, 7, 8, 9}));
}

TEST_F(PinnedBufferTest, PruneReleasesUncoveredElements)
{
  Buffer buffer(5);
  bool pinned = true;
  buffer.setEvictionGuard(
    [&pinned](const Buffer::TimePoint & t) {return pinned && t <= at(2);}, 10);
  fill(buffer, 0, 9);
  EXPECT_EQ(buffer.retainedCount(), 3u);

  EXPECT_EQ(buffer.pruneRetained(), 0u);  // Still pinned
  pinned = false;
  EXPECT_EQ(buffer.pruneRetained(), 3u);
  EXPECT_EQ(buffer.retainedCount(), 0u);
  EXPECT_EQ(buffer.getRange(at(0), at(9)).size(), 5u);
}

TEST_F(PinnedBufferTest, RetainedStoreRespectsBudget)
{
  Buffer buffer(4);
  buffer.setEvictionGuard([](const Buffer::TimePoint &) {return true;}, 2);
  fill(buffer, 0, 9);

  // 6 evictions, budget of 2: the newest two protected elements are kept
  EXPECT_EQ(buffer.retainedCount(), 2u);
  EXPECT_EQ(buffer.retainedDroppedCount(), 4u);
  EXPECT_EQ(buffer.oldestTimestamp(), at(4));
}

TEST_F(PinnedBufferTest, ZeroBudgetDisablesRetention)
{
  Buffer buffer(3);
  buffer.setEvictionGuard([](const Buffer::TimePoint &) {return true;}, 0);
  fill(buffer, 0, 5);
  EXPECT_EQ(buffer.retainedCount(), 0u);
  EXPECT_EQ(buffer.droppedCount(), 3u);
}

TEST_F(PinnedBufferTest, ClearDropsRetained)
{
  Buffer buffer(2);
  buffer.setEvictionGuard([](const Buffer::TimePoint &) {return true;}, 5);
  fill(buffer, 0, 4);
  buffer.clear();
  EXPECT_EQ(buffer.retainedCount(), 0u);
  EXPECT_TRUE(buffer.getRange(at(0), at(9)).empty());
}

TEST_F(PinnedBufferTest, GetRecentWithExplicitNow)
{
  // Sim-time style stamps near the epoch; wall-clock now() would miss them all
  Buffer buffer(10);
  fill(buffer, 0, 9);
  auto recent = buffer.getRecent(duration<double>(3.0), at(9));
  ASSERT_EQ(recent.size(), 4u);
  EXPECT_EQ(recent.front().data, 6);
  EXPECT_TRUE(buffer.getRecent(duration<double>(3.0)).empty());
}

int main(int argc, char ** argv)
{
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
