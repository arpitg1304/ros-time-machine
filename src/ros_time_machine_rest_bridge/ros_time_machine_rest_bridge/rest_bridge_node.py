#!/usr/bin/env python3
"""
REST API Bridge for ROS Time Machine

This node provides a REST API interface to trigger Time Machine snapshots
without requiring ROS tools. Perfect for web dashboards, mobile apps, and
remote operation.
"""

import asyncio
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("ERROR: FastAPI dependencies not installed")
    print("Install with: pip3 install fastapi uvicorn pydantic")
    raise


class TriggerRequest(BaseModel):
    """Request model for triggering a snapshot"""
    reason: Optional[str] = "manual_api_trigger"
    metadata: Optional[Dict] = None


class TriggerResponse(BaseModel):
    """Response model for trigger requests"""
    success: bool
    snapshot_id: str
    message: str
    timestamp: str


class SnapshotMetadata(BaseModel):
    """Metadata about a snapshot"""
    snapshot_id: str
    timestamp: str
    reason: str
    status: str  # "triggered", "recording", "complete"
    metadata: Optional[Dict] = None


class RestBridgeNode(Node):
    """ROS 2 node that bridges REST API to Time Machine"""

    def __init__(self):
        super().__init__('rest_bridge_node')

        # Declare parameters
        self.declare_parameter('host', '0.0.0.0')
        self.declare_parameter('port', 8080)
        self.declare_parameter('trigger_topic', '/time_machine/manual_trigger')
        self.declare_parameter('snapshot_dir', '/tmp/ros_time_machine/snapshots')

        # Get parameters
        self.host = self.get_parameter('host').value
        self.port = self.get_parameter('port').value
        self.trigger_topic = self.get_parameter('trigger_topic').value
        self.snapshot_dir = Path(self.get_parameter('snapshot_dir').value)

        # Create trigger publisher
        self.trigger_publisher = self.create_publisher(
            Bool,
            self.trigger_topic,
            10
        )

        # Snapshot tracking
        self.snapshots: Dict[str, SnapshotMetadata] = {}

        self.get_logger().info(f'REST Bridge initialized')
        self.get_logger().info(f'  Host: {self.host}:{self.port}')
        self.get_logger().info(f'  Trigger topic: {self.trigger_topic}')
        self.get_logger().info(f'  Snapshot directory: {self.snapshot_dir}')

    def trigger_snapshot(self, reason: str = "api_trigger", metadata: Optional[Dict] = None) -> SnapshotMetadata:
        """
        Trigger a snapshot recording

        Args:
            reason: Reason for triggering the snapshot
            metadata: Additional metadata to store

        Returns:
            SnapshotMetadata object with snapshot information
        """
        # Generate unique snapshot ID
        snapshot_id = str(uuid.uuid4())[:8]
        timestamp = datetime.now().isoformat()

        # Create snapshot metadata
        snapshot_meta = SnapshotMetadata(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            reason=reason,
            status="triggered",
            metadata=metadata or {}
        )

        # Store metadata
        self.snapshots[snapshot_id] = snapshot_meta

        # Publish trigger message
        msg = Bool()
        msg.data = True
        self.trigger_publisher.publish(msg)

        self.get_logger().info(
            f'Snapshot triggered: {snapshot_id} - {reason}'
        )

        return snapshot_meta

    def list_snapshots(self) -> List[SnapshotMetadata]:
        """Get list of all snapshots"""
        return list(self.snapshots.values())

    def get_snapshot(self, snapshot_id: str) -> Optional[SnapshotMetadata]:
        """Get snapshot by ID"""
        return self.snapshots.get(snapshot_id)


# Global FastAPI app and ROS node
app = FastAPI(
    title="ROS Time Machine REST API",
    description="REST API bridge for triggering and managing Time Machine snapshots",
    version="0.1.0"
)
ros_node: Optional[RestBridgeNode] = None


@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "name": "ROS Time Machine REST API",
        "version": "0.1.0",
        "status": "online",
        "endpoints": {
            "health": "/health",
            "trigger": "POST /api/snapshots/trigger",
            "list": "GET /api/snapshots",
            "get": "GET /api/snapshots/{snapshot_id}"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "ros_node": ros_node is not None,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/api/snapshots/trigger", response_model=TriggerResponse)
async def trigger_snapshot(request: TriggerRequest):
    """
    Trigger a new snapshot recording

    Example:
        curl -X POST http://localhost:8080/api/snapshots/trigger \\
             -H "Content-Type: application/json" \\
             -d '{"reason": "collision_detected", "metadata": {"severity": "high"}}'
    """
    if ros_node is None:
        raise HTTPException(status_code=500, detail="ROS node not initialized")

    try:
        snapshot_meta = ros_node.trigger_snapshot(
            reason=request.reason,
            metadata=request.metadata
        )

        return TriggerResponse(
            success=True,
            snapshot_id=snapshot_meta.snapshot_id,
            message=f"Snapshot triggered successfully",
            timestamp=snapshot_meta.timestamp
        )
    except Exception as e:
        ros_node.get_logger().error(f'Failed to trigger snapshot: {e}')
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/snapshots", response_model=List[SnapshotMetadata])
async def list_snapshots():
    """
    Get list of all snapshots

    Example:
        curl http://localhost:8080/api/snapshots
    """
    if ros_node is None:
        raise HTTPException(status_code=500, detail="ROS node not initialized")

    return ros_node.list_snapshots()


@app.get("/api/snapshots/{snapshot_id}", response_model=SnapshotMetadata)
async def get_snapshot(snapshot_id: str):
    """
    Get snapshot metadata by ID

    Example:
        curl http://localhost:8080/api/snapshots/abc123
    """
    if ros_node is None:
        raise HTTPException(status_code=500, detail="ROS node not initialized")

    snapshot = ros_node.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return snapshot


def run_fastapi_server(host: str, port: int):
    """Run FastAPI server in separate thread"""
    uvicorn.run(app, host=host, port=port, log_level="info")


def main(args=None):
    """Main entry point"""
    global ros_node

    # Initialize ROS
    rclpy.init(args=args)

    # Create ROS node
    ros_node = RestBridgeNode()

    # Start FastAPI server in separate thread
    api_thread = threading.Thread(
        target=run_fastapi_server,
        args=(ros_node.host, ros_node.port),
        daemon=True
    )
    api_thread.start()

    ros_node.get_logger().info('=' * 60)
    ros_node.get_logger().info('REST API Bridge is running!')
    ros_node.get_logger().info(f'  API endpoint: http://{ros_node.host}:{ros_node.port}')
    ros_node.get_logger().info(f'  Docs: http://{ros_node.host}:{ros_node.port}/docs')
    ros_node.get_logger().info('=' * 60)
    ros_node.get_logger().info('')
    ros_node.get_logger().info('Try it:')
    ros_node.get_logger().info(f'  curl http://localhost:{ros_node.port}/health')
    ros_node.get_logger().info(f'  curl -X POST http://localhost:{ros_node.port}/api/snapshots/trigger')
    ros_node.get_logger().info('')

    try:
        # Spin ROS node
        rclpy.spin(ros_node)
    except KeyboardInterrupt:
        pass
    finally:
        ros_node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
