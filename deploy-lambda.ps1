
param(
    [string]$Profile = "flowtasks-dev",
    [string]$Region = "ap-south-1",
    [string]$RoleArn = "arn:aws:iam::373220260877:role/FlowTasks-Dev-Lambda-ExecutionRole"
)

$ErrorActionPreference = "Stop"

$Functions = @(
    @{ Name = "TaskList";       Source = "backend/task/task-list.py" },
    @{ Name = "TaskUpdate";     Source = "backend/task/task-update.py" },
    @{ Name = "TaskDelete";     Source = "backend/task/task-delete.py" },
    @{ Name = "AuditGet";       Source = "backend/audit/audit-get-lambda.py" },
    @{ Name = "AddComment";     Source = "backend/commnents/add-commnets.py" },
    @{ Name = "GetComments";    Source = "backend/commnents/get_comments.py" },
    @{ Name = "EditComment";    Source = "backend/commnents/edit_comment.py" },
    @{ Name = "DeleteComment";  Source = "backend/commnents/delete_comment.py" },
    @{ Name = "GetMentions";    Source = "backend/mentions/getmention.py" },
    @{ Name = "MarkMentionRead";Source = "backend/mentions/markmentionread.py" },
    @{ Name = "GetUsers";       Source = "backend/users/get-users.py" }
)

$PackageRoot = Join-Path $PSScriptRoot "lambda-packages"
New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

foreach ($Function in $Functions) {

    $FunctionName = "FlowTasks-Dev-$($Function.Name)"
    $SourcePath = Join-Path $PSScriptRoot $Function.Source

    if (!(Test-Path $SourcePath)) {
        throw "Source file not found: $SourcePath"
    }

    $PackageFolder = Join-Path $PackageRoot $Function.Name
    $ZipPath = Join-Path $PackageRoot "$($Function.Name).zip"

    New-Item -ItemType Directory -Force -Path $PackageFolder | Out-Null

    Copy-Item $SourcePath (Join-Path $PackageFolder "lambda_function.py") -Force

    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }

    Compress-Archive `
        -Path (Join-Path $PackageFolder "lambda_function.py") `
        -DestinationPath $ZipPath `
        -Force

    Write-Host "`nDeploying $FunctionName ..." -ForegroundColor Cyan

    aws lambda create-function `
        --function-name $FunctionName `
        --runtime python3.12 `
        --handler lambda_function.handler `
        --role $RoleArn `
        --zip-file "fileb://$ZipPath" `
        --timeout 30 `
        --memory-size 256 `
        --region $Region `
        --profile $Profile

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $FunctionName"
    }

    Write-Host "Created $FunctionName" -ForegroundColor Green
}

Write-Host "`nDeployment completed." -ForegroundColor Green
