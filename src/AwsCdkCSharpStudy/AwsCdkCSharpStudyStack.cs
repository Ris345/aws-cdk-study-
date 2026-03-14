using System.Collections.Generic;
using Amazon.CDK;
using Amazon.CDK.AWS.Events;
using Amazon.CDK.AWS.Events.Targets;
using Amazon.CDK.AWS.IAM;
using Amazon.CDK.AWS.Lambda;
using Amazon.CDK.AWS.S3;
using Constructs;

namespace AwsCdkCSharpStudy
{
    public class AwsCdkCSharpStudyStack : Stack
    {
        internal AwsCdkCSharpStudyStack(Construct scope, string id, IStackProps props = null) : base(scope, id, props)
        {
            // ── S3 bucket ────────────────────────────────────────────────────────────
            // Stores cost reports at: cost-reports/YYYY-MM/report.json
            var reportsBucket = new Bucket(this, "StudyBucket", new BucketProps
            {
                Versioned = true,
                RemovalPolicy = RemovalPolicy.RETAIN,
                Encryption = BucketEncryption.S3_MANAGED,
                BlockPublicAccess = BlockPublicAccess.BLOCK_ALL,
                EnforceSSL = true,
            });

            // ── Lambda function ──────────────────────────────────────────────────────
            // Reads cost data for the previous month and uploads a JSON report to S3.
            // Source: lambda/cost_tracker.py (relative to cdk.json location)
            var costTrackerFn = new Function(this, "CostTrackerFunction", new FunctionProps
            {
                Runtime = Runtime.PYTHON_3_12,
                Handler = "cost_tracker.handler",
                Code = Code.FromAsset("lambda"),
                Timeout = Duration.Seconds(60),
                Description = "Fetches previous-month AWS costs via Cost Explorer and uploads a JSON report to S3",
                Environment = new Dictionary<string, string>
                {
                    // Passes the auto-generated bucket name into the function at runtime
                    ["BUCKET_NAME"] = reportsBucket.BucketName,
                },
            });

            // ── IAM: S3 write access ─────────────────────────────────────────────────
            // Grants s3:PutObject (and s3:PutObjectAcl) on the bucket to the Lambda role.
            reportsBucket.GrantPut(costTrackerFn);

            // ── IAM: Cost Explorer read access ───────────────────────────────────────
            // Cost Explorer does not support resource-level permissions; "*" is required.
            costTrackerFn.AddToRolePolicy(new PolicyStatement(new PolicyStatementProps
            {
                Effect = Effect.ALLOW,
                Actions = new[] { "ce:GetCostAndUsage" },
                Resources = new[] { "*" },
            }));

            // ── EventBridge rule ─────────────────────────────────────────────────────
            // Triggers on the last day of every month at 23:00 UTC.
            // "L" in the Day field is an EventBridge cron special character meaning "last day of month".
            // WeekDay must be "?" whenever Day is set to a specific value.
            // Assigned to a variable so we can pass its name/ARN to the smoke test.
            var costTrackerRule = new Rule(this, "CostTrackerSchedule", new RuleProps
            {
                Schedule = Schedule.Cron(new CronOptions
                {
                    Minute = "0",
                    Hour = "23",
                    Day = "L",
                    Month = "*",
                }),
                Targets = new[] { new LambdaFunction(costTrackerFn) },
                Description = "Trigger CostTrackerFunction on the last day of each month at 23:00 UTC",
            });

            // ── Smoke test Lambda ────────────────────────────────────────────────────
            // Verifies the architecture wiring is intact on demand:
            //   1. S3 bucket is reachable and writable
            //   2. Cost Explorer permission is intact
            //   3. EventBridge rule is ENABLED
            var smokeTestFn = new Function(this, "SmokeTestFunction", new FunctionProps
            {
                Runtime = Runtime.PYTHON_3_12,
                Handler = "smoke_test.handler",
                Code = Code.FromAsset("lambda"),
                Timeout = Duration.Seconds(30),
                Description = "On-demand smoke test — verifies S3, Cost Explorer, and EventBridge wiring",
                Environment = new Dictionary<string, string>
                {
                    ["BUCKET_NAME"] = reportsBucket.BucketName,
                    ["RULE_NAME"] = costTrackerRule.RuleName,
                },
            });

            // ── IAM: smoke test needs read+write on the bucket (HeadBucket, PutObject, DeleteObject)
            reportsBucket.GrantReadWrite(smokeTestFn);

            // ── IAM: smoke test needs Cost Explorer read access
            smokeTestFn.AddToRolePolicy(new PolicyStatement(new PolicyStatementProps
            {
                Effect = Effect.ALLOW,
                Actions = new[] { "ce:GetCostAndUsage" },
                Resources = new[] { "*" },
            }));

            // ── IAM: smoke test needs to inspect the EventBridge rule state
            smokeTestFn.AddToRolePolicy(new PolicyStatement(new PolicyStatementProps
            {
                Effect = Effect.ALLOW,
                Actions = new[] { "events:DescribeRule" },
                Resources = new[] { costTrackerRule.RuleArn },
            }));

            // ── Function URL ─────────────────────────────────────────────────────────
            // Exposes the smoke test as a plain HTTPS endpoint — invoke with a curl.
            // No auth required since this only reads infra state and writes a harmless probe object.
            var smokeTestUrl = smokeTestFn.AddFunctionUrl(new FunctionUrlOptions
            {
                AuthType = FunctionUrlAuthType.NONE,
            });

            // ── CloudFormation outputs ───────────────────────────────────────────────
            new CfnOutput(this, "ReportsBucketName", new CfnOutputProps
            {
                Value = reportsBucket.BucketName,
                Description = "S3 bucket that stores monthly cost reports (reports/YYYY-MM.json)",
            });

            new CfnOutput(this, "SmokeTestUrl", new CfnOutputProps
            {
                Value = smokeTestUrl.Url,
                Description = "GET this URL to run the smoke test (returns 200 ok or 503 degraded)",
            });
        }
    }
}


