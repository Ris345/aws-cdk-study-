# AWS CDK C# Study Project

A hands-on project for learning AWS infrastructure provisioning using AWS CDK with C#. The stack currently provisions a secure S3 bucket and will grow to cover more AWS services over time.

## Stack

- **S3 Bucket** — versioned, encrypted at rest (SSE-S3), all public access blocked, SSL enforced

## CI/CD

CircleCI pipeline with four stages:
1. **build-and-synth** — compiles the project and validates the CloudFormation template (runs on every push, no AWS credentials required)
2. **diff** — compares the local stack against what is live in AWS (runs on every push)
3. **hold-for-approval** — manual gate before any deployment (main branch only)
4. **deploy** — bootstraps and deploys the stack to AWS (main branch only, after approval)

AWS credentials are stored in a CircleCI context and never committed to the repo.

## Commands

- `dotnet build src` — compile the project
- `cdk synth` — emit the CloudFormation template
- `cdk diff` — compare local stack with deployed stack
- `cdk deploy` — deploy to AWS
- `cdk destroy` — tear down the stack (bucket is retained by default)
