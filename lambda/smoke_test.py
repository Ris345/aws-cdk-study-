"""
smoke_test.py

Lambda that verifies the CostTracker architecture wiring is intact.

Checks:
  1. S3 bucket is reachable and writable (writes + deletes a tiny probe object)
  2. Cost Explorer permission is intact (GetCostAndUsage for a 1-day window)
  3. EventBridge rule is ENABLED

Environment variables:
    BUCKET_NAME: S3 bucket to probe
    RULE_NAME:   EventBridge rule name to verify
"""

import json
import logging
import os
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SMOKE_KEY = "smoke-test/ping.txt"


def _check_s3(bucket_name: str) -> dict:
    """Verify the bucket exists and we can write to it."""
    s3 = boto3.client("s3")

    # HeadBucket confirms the bucket exists and we have access
    try:
        s3.head_bucket(Bucket=bucket_name)
    except ClientError as e:
        return {"ok": False, "error": f"HeadBucket failed: {e.response['Error']['Code']}"}

    # Write then immediately delete a tiny probe object to confirm PutObject works
    try:
        s3.put_object(
            Bucket=bucket_name,
            Key=SMOKE_KEY,
            Body=b"smoke-test-ok",
            ContentType="text/plain",
        )
        s3.delete_object(Bucket=bucket_name, Key=SMOKE_KEY)
    except ClientError as e:
        return {"ok": False, "error": f"PutObject/DeleteObject failed: {e.response['Error']['Code']}"}

    return {"ok": True}


def _check_cost_explorer() -> dict:
    """Verify ce:GetCostAndUsage permission is intact with a minimal 1-day query."""
    ce = boto3.client("ce", region_name="us-east-1")
    today = date.today()
    yesterday = today - timedelta(days=1)

    try:
        ce.get_cost_and_usage(
            TimePeriod={
                "Start": yesterday.strftime("%Y-%m-%d"),
                "End": today.strftime("%Y-%m-%d"),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
    except ClientError as e:
        return {"ok": False, "error": f"GetCostAndUsage failed: {e.response['Error']['Code']}"}

    return {"ok": True}


def _check_eventbridge_rule(rule_name: str) -> dict:
    """Verify the EventBridge cron rule exists and is ENABLED."""
    events = boto3.client("events")

    try:
        response = events.describe_rule(Name=rule_name)
        state = response.get("State", "UNKNOWN")
        if state != "ENABLED":
            return {"ok": False, "error": f"Rule state is '{state}', expected 'ENABLED'"}
    except ClientError as e:
        return {"ok": False, "error": f"DescribeRule failed: {e.response['Error']['Code']}"}

    return {"ok": True}


def handler(event, context):
    bucket_name = os.environ.get("BUCKET_NAME", "").strip()
    rule_name = os.environ.get("RULE_NAME", "").strip()

    missing = [v for v, val in [("BUCKET_NAME", bucket_name), ("RULE_NAME", rule_name)] if not val]
    if missing:
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "missing_env_vars": missing}),
        }

    checks = {
        "s3": _check_s3(bucket_name),
        "cost_explorer": _check_cost_explorer(),
        "eventbridge_rule": _check_eventbridge_rule(rule_name),
    }

    all_ok = all(c["ok"] for c in checks.values())
    status = "ok" if all_ok else "degraded"

    logger.info("Smoke test result: %s | checks=%s", status, checks)

    return {
        "statusCode": 200 if all_ok else 503,
        "body": json.dumps({"status": status, "checks": checks}),
    }
