
$ErrorActionPreference = "Stop"

$Region = "ap-south-1"
$Profile = "flowtasks-dev"
$ApiId = "rq067no9cb"
$AuthorizerId = "gwczmv"
$AccountId = "373220260877"

$Routes = @(
    @{ Method = "GET";    Path = "/tasks";                                      Function = "TaskList" },
    @{ Method = "POST";   Path = "/tasks";                                      Function = "TaskCreate" },
    @{ Method = "PUT";    Path = "/tasks/{id}";                                 Function = "TaskUpdate" },
    @{ Method = "DELETE"; Path = "/tasks/{id}";                                 Function = "TaskDelete" },
    @{ Method = "GET";    Path = "/tasks/{taskId}/comments";                    Function = "GetComments" },
    @{ Method = "POST";   Path = "/tasks/{taskId}/comments";                    Function = "AddComment" },
    @{ Method = "PUT";    Path = "/tasks/{taskId}/comments/{commentId}";        Function = "EditComment" },
    @{ Method = "DELETE"; Path = "/tasks/{taskId}/comments/{commentId}";        Function = "DeleteComment" },
    @{ Method = "GET";    Path = "/mentions";                                   Function = "GetMentions" },
    @{ Method = "PATCH";  Path = "/mentions/{sk}";                              Function = "MarkMentionRead" },
    @{ Method = "GET";    Path = "/audit";                                      Function = "AuditGet" },
    @{ Method = "GET";    Path = "/users";                                      Function = "GetUsers" }
)

foreach ($Route in $Routes) {

    $FunctionName = "FlowTasks-Dev-$($Route.Function)"
    $RouteKey = "$($Route.Method) $($Route.Path)"

    Write-Host "`nProcessing $RouteKey -> $FunctionName" -ForegroundColor Cyan

    # Get Lambda ARN
    $LambdaArn = aws lambda get-function-configuration `
        --function-name $FunctionName `
        --query FunctionArn `
        --output text `
        --region $Region `
        --profile $Profile

    if ($LASTEXITCODE -ne 0 -or !$LambdaArn) {
        throw "Could not retrieve Lambda ARN: $FunctionName"
    }

    # Create Lambda integration
    $IntegrationOutput = aws apigatewayv2 create-integration `
        --api-id $ApiId `
        --integration-type AWS_PROXY `
        --integration-uri $LambdaArn `
        --payload-format-version "2.0" `
        --timeout-in-millis 30000 `
        --region $Region `
        --profile $Profile

    if ($LASTEXITCODE -ne 0) {
        throw "Integration creation failed: $FunctionName"
    }

    $Integration = $IntegrationOutput | ConvertFrom-Json
    $IntegrationId = $Integration.IntegrationId

    # Allow API Gateway to invoke Lambda
    $StatementId = "ApiGatewayInvoke-$($Route.Function)"

    aws lambda add-permission `
        --function-name $FunctionName `
        --statement-id $StatementId `
        --action lambda:InvokeFunction `
        --principal apigateway.amazonaws.com `
        --source-arn "arn:aws:execute-api:${Region}:${AccountId}:${ApiId}/*/*/*" `
        --region $Region `
        --profile $Profile | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "Lambda permission failed: $FunctionName"
    }

    # Create JWT-protected API route
    aws apigatewayv2 create-route `
        --api-id $ApiId `
        --route-key $RouteKey `
        --target "integrations/$IntegrationId" `
        --authorization-type JWT `
        --authorizer-id $AuthorizerId `
        --region $Region `
        --profile $Profile | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "Route creation failed: $RouteKey"
    }

    Write-Host "Created: $RouteKey" -ForegroundColor Green
}

# Create automatically deployed default stage
aws apigatewayv2 create-stage `
    --api-id $ApiId `
    --stage-name '$default' `
    --auto-deploy `
    --region $Region `
    --profile $Profile

if ($LASTEXITCODE -ne 0) {
    throw "Default stage creation failed. Check whether it already exists."
}

Write-Host "`nAll API routes and integrations created successfully." -ForegroundColor Green
