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

#include "ros_time_machine/pin_manager.hpp"

using ros_time_machine::PinManager;

namespace
{
constexpr int64_t kSec = 1000000000LL;
}

TEST(PinManagerTest, AddAndCover)
{
  PinManager pins;
  uint64_t id = pins.add(10 * kSec, 20 * kSec, 5.0, 12 * kSec, "manipulation", "force spike");
  EXPECT_NE(id, 0u);
  EXPECT_EQ(pins.activeCount(), 1u);
  EXPECT_TRUE(pins.covers(10 * kSec));
  EXPECT_TRUE(pins.covers(15 * kSec));
  EXPECT_TRUE(pins.covers(20 * kSec));
  EXPECT_FALSE(pins.covers(9 * kSec));
  EXPECT_FALSE(pins.covers(21 * kSec));
}

TEST(PinManagerTest, ReversedRangeIsNormalized)
{
  PinManager pins;
  uint64_t id = pins.add(20 * kSec, 10 * kSec, 5.0, 0);
  auto pin = pins.take(id);
  ASSERT_TRUE(pin.has_value());
  EXPECT_EQ(pin->start_ns, 10 * kSec);
  EXPECT_EQ(pin->end_ns, 20 * kSec);
}

TEST(PinManagerTest, TakeRemovesPinOnce)
{
  PinManager pins;
  uint64_t id = pins.add(0, kSec, 5.0, 0, "safety");
  auto pin = pins.take(id);
  ASSERT_TRUE(pin.has_value());
  EXPECT_EQ(pin->profile, "safety");
  EXPECT_FALSE(pins.take(id).has_value());
  EXPECT_FALSE(pins.covers(0));
  EXPECT_EQ(pins.activeCount(), 0u);
}

TEST(PinManagerTest, GetDoesNotRemove)
{
  PinManager pins;
  uint64_t id = pins.add(0, kSec, 5.0, 0, "safety");
  ASSERT_TRUE(pins.get(id).has_value());
  EXPECT_EQ(pins.get(id)->profile, "safety");
  EXPECT_TRUE(pins.covers(0));
  EXPECT_FALSE(pins.get(id + 100).has_value());
}

TEST(PinManagerTest, ExpiresAfterTtl)
{
  PinManager pins;
  uint64_t short_id = pins.add(0, kSec, 2.0, 100 * kSec);
  uint64_t long_id = pins.add(0, kSec, 10.0, 100 * kSec);

  EXPECT_TRUE(pins.takeExpired(101 * kSec).empty());
  auto expired = pins.takeExpired(102 * kSec);
  ASSERT_EQ(expired.size(), 1u);
  EXPECT_EQ(expired[0].id, short_id);
  EXPECT_EQ(pins.activeCount(), 1u);
  EXPECT_TRUE(pins.take(long_id).has_value());
}

TEST(PinManagerTest, ExpiredLeavesPinsInPlace)
{
  PinManager pins;
  uint64_t id = pins.add(0, kSec, 1.0, 0);
  pins.add(0, kSec, 10.0, 0);
  auto expired = pins.expired(2 * kSec);
  ASSERT_EQ(expired.size(), 1u);
  EXPECT_EQ(expired[0].id, id);
  EXPECT_EQ(pins.activeCount(), 2u);
  EXPECT_TRUE(pins.covers(0));
  EXPECT_TRUE(pins.take(id).has_value());
}

TEST(PinManagerTest, DefaultTtlUsedWhenNonPositive)
{
  PinManager pins;
  pins.setDefaultTtl(3.0);
  pins.add(0, kSec, 0.0, 0);
  EXPECT_TRUE(pins.takeExpired(2 * kSec).empty());
  EXPECT_EQ(pins.takeExpired(3 * kSec).size(), 1u);
}

TEST(PinManagerTest, IdsAreUnique)
{
  PinManager pins;
  uint64_t a = pins.add(0, kSec, 1.0, 0);
  uint64_t b = pins.add(0, kSec, 1.0, 0);
  EXPECT_NE(a, b);
  EXPECT_EQ(pins.list().size(), 2u);
}

int main(int argc, char ** argv)
{
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
