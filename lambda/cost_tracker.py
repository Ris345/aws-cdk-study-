"""
cost_tracker.py

Lambda function that queries AWS Cost Explorer for the previous month's costs,
groups them by "User" and "Team" resource tags, and uploads a JSON report to S3
at reports/YYYY-MM.json.

Environment variables:
    BUCKET_NAME: Name of the S3 bucket to upload the report to.

IAM permissions required:
    - ce:GetCostAndUsage (Cost Explorer, resource: *)
    - s3:PutObject on the target bucket
"""

# json  -> turns Python dicts into JSON strings (and back)
# logging -> lets us print messages that show up in CloudWatch Logs
# os    -> lets us read environment variables like BUCKET_NAME
# datetime stuff -> used to calculate "what month was last month?"
import json
import logging
import os
from datetime import date, timedelta, datetime

# boto3 is the official AWS SDK for Python — it's how we talk to AWS services
import boto3
# ClientError is the exception boto3 raises when an AWS API call fails
from botocore.exceptions import ClientError

# Set up a logger so our print-style messages show up in CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)  # INFO means we'll see info, warning, and error messages


def _get_previous_month_range() -> tuple[str, str, str]:
    """
    Works out the start date, end date, and label for last month.
    Example: if today is 2026-03-15, it returns ('2026-02-01', '2026-03-01', '2026-02')
    """
    today = date.today()

    # Replace the day with 1 to get the first day of the current month
    # e.g. 2026-03-15 -> 2026-03-01
    first_of_this_month = today.replace(day=1)

    # Subtract one day from the 1st of this month to land on the last day of last month
    # e.g. 2026-03-01 - 1 day = 2026-02-28
    last_day_of_prev_month = first_of_this_month - timedelta(days=1)

    # Now snap back to the 1st of that month to get our start date
    # e.g. 2026-02-28 -> 2026-02-01
    first_of_prev_month = last_day_of_prev_month.replace(day=1)

    # Format dates as strings that the Cost Explorer API understands (YYYY-MM-DD)
    start_date = first_of_prev_month.strftime("%Y-%m-%d")   # e.g. "2026-02-01"
    end_date = first_of_this_month.strftime("%Y-%m-%d")     # e.g. "2026-03-01" (end is exclusive in CE)
    month_label = first_of_prev_month.strftime("%Y-%m")     # e.g. "2026-02" — used in the S3 file path

    return start_date, end_date, month_label


def _fetch_costs(start_date: str, end_date: str) -> dict:
    """
    Asks AWS Cost Explorer for last month's costs grouped by User tag then Team tag.
    Cost Explorer allows up to 2 GroupBy entries; both use Type "TAG".
    Returns the raw response from AWS — we'll parse it in _build_report().
    """
    # Cost Explorer is a special global AWS service that only exists in us-east-1.
    # Even if our Lambda runs in another region, we must point the client here.
    ce = boto3.client("ce", region_name="us-east-1")

    logger.info("Calling Cost Explorer: start=%s end=%s", start_date, end_date)

    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start_date, "End": end_date},
        Granularity="MONTHLY",          # give me one total per month (not daily)
        Metrics=["UnblendedCost"],      # UnblendedCost = the actual list price you pay
        GroupBy=[
            # CE returns tag groups with keys in "TagKey$TagValue" format.
            # e.g. Department "Engineering" -> key "Department$Engineering"; untagged -> "Department$"
            # Exact key names can be confirmed in Cost Explorer UI -> "Group by tag" dropdown.
            {"Type": "TAG", "Key": "Department"},
            {"Type": "TAG", "Key": "Division"},
        ],
    )
    return response


def _build_report(month_label: str, ce_response: dict) -> dict:
    """
    Takes the raw Cost Explorer response and builds a clean, readable dict.
    Each row is one (user, team) tag combination with its cost for the month.
    """
    breakdown = []
    total_cost = 0.0
    currency = "USD"

    # The response has a list called "ResultsByTime" — one entry per time window.
    # Since we asked for MONTHLY granularity, there will be exactly one entry.
    for time_period in ce_response.get("ResultsByTime", []):
        # Each "Group" is one (user, team) tag combination and its cost.
        for group in time_period.get("Groups", []):
            # CE returns tag keys as "TagKey$TagValue" — split on the first "$" to get the value.
            # If a resource has no tag, the value part is an empty string.
            dept_raw, div_raw = group["Keys"][0], group["Keys"][1]
            department = dept_raw.split("$", 1)[1] or "untagged"
            division = div_raw.split("$", 1)[1] or "untagged"

            metric = group["Metrics"]["UnblendedCost"]
            amount = float(metric["Amount"])
            currency = metric["Unit"]   # almost always "USD"
            total_cost += amount

            breakdown.append({
                "department": department,
                "division": division,
                "cost": round(amount, 6),
                "unit": currency,
            })

    # Sort from most expensive to cheapest — easiest to scan at a glance
    breakdown.sort(key=lambda row: row["cost"], reverse=True)

    return {
        "month": month_label,                                           # e.g. "2026-02"
        "total_cost": round(total_cost, 6),                             # grand total
        "currency": currency,                                           # "USD"
        "breakdown": breakdown,                                         # per (user, team) costs
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _upload_to_s3(bucket_name: str, month_label: str, report: dict) -> str:
    """
    Converts the report dict to a JSON string and saves it to S3.
    The file path in the bucket will look like: reports/2026-02.json
    Returns that file path (called an "S3 key") so we can log it.
    """
    s3 = boto3.client("s3")

    # Build the path inside the bucket — one file per month, no sub-folders needed
    s3_key = f"reports/{month_label}.json"

    s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=json.dumps(report, indent=2),  # indent=2 makes the JSON file human-readable
        ContentType="application/json",     # tells S3 what kind of file this is
    )

    logger.info("Report uploaded to s3://%s/%s", bucket_name, s3_key)
    return s3_key


def handler(event, context):
    """
    This is the function AWS Lambda calls when the function is triggered.
    'event' contains any input data passed to the function.
    'context' contains runtime info like the function name and timeout remaining.
    We always return a dict with a statusCode (like HTTP) and a body.
    """

    # ── Step 1: Check that BUCKET_NAME was set in the Lambda environment ──────
    # os.environ.get() reads an environment variable; returns "" if it's missing
    bucket_name = os.environ.get("BUCKET_NAME", "").strip()
    if not bucket_name:
        logger.error("BUCKET_NAME environment variable is missing or empty")
        # Return early with a 500 error — nothing else can work without the bucket name
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "BUCKET_NAME environment variable is not configured"}),
        }

    # ── Step 2: Figure out which month we're reporting on ────────────────────
    start_date, end_date, month_label = _get_previous_month_range()
    logger.info("Generating cost report for month=%s (%s to %s)", month_label, start_date, end_date)

    # ── Step 3: Call Cost Explorer to get the actual cost data ───────────────
    # We wrap this in try/except so a failed AWS call doesn't crash the function silently
    try:
        ce_response = _fetch_costs(start_date, end_date)
    except ClientError as exc:
        # ClientError means AWS responded but told us something went wrong
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]

        if error_code == "LimitExceededException":
            # We called Cost Explorer too many times too fast — back off and retry later
            logger.warning("Cost Explorer rate limit hit: %s", error_msg)
            return {
                "statusCode": 429,  # 429 = "Too Many Requests"
                "body": json.dumps({"error": "Cost Explorer rate limit exceeded", "detail": error_msg}),
            }

        # Any other AWS error (e.g. permissions problem)
        logger.error("Cost Explorer ClientError [%s]: %s", error_code, error_msg)
        return {
            "statusCode": 502,  # 502 = "Bad Gateway" — upstream service failed
            "body": json.dumps({"error": "Failed to retrieve cost data", "code": error_code}),
        }
    except Exception as exc:  # noqa: BLE001 — catch anything truly unexpected
        logger.error("Unexpected error fetching costs: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Unexpected error fetching cost data"}),
        }

    # ── Step 4: Turn the raw AWS response into our clean report format ───────
    report = _build_report(month_label, ce_response)
    logger.info(
        "Report built: month=%s total_cost=%.4f rows=%d",
        month_label,
        report["total_cost"],
        len(report["breakdown"]),
    )

    # ── Step 5: Save the report JSON file to S3 ──────────────────────────────
    try:
        s3_key = _upload_to_s3(bucket_name, month_label, report)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        error_msg = exc.response["Error"]["Message"]
        logger.error("S3 upload failed [%s]: %s", error_code, error_msg)
        return {
            "statusCode": 502,
            "body": json.dumps({"error": "Failed to upload report to S3", "code": error_code}),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error uploading to S3: %s", exc, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Unexpected error uploading report"}),
        }

    # ── Step 6: Everything worked — return a success response ────────────────
    return {
        "statusCode": 200,  # 200 = "OK"
        "body": json.dumps({
            "message": "Cost report generated and uploaded successfully",
            "month": month_label,
            "total_cost": report["total_cost"],
            "currency": report["currency"],
            "s3_key": s3_key,                              # where to find the file in S3
            "row_count": len(report["breakdown"]),
        }),
    }
