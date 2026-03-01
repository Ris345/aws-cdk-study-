using Amazon.CDK;
using Amazon.CDK.AWS.S3;
using Constructs;

namespace AwsCdkCSharpStudy
{
    public class AwsCdkCSharpStudyStack : Stack
    {
        internal AwsCdkCSharpStudyStack(Construct scope, string id, IStackProps props = null) : base(scope, id, props)
        {
            new Bucket(this, "StudyBucket", new BucketProps
            {
                Versioned = true,
                RemovalPolicy = RemovalPolicy.RETAIN,
                Encryption = BucketEncryption.S3_MANAGED,
                BlockPublicAccess = BlockPublicAccess.BLOCK_ALL,
                EnforceSSL = true,
            });
        }
    }
}


