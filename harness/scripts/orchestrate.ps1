param(
    [Parameter(Mandatory = $true)]
    [string]$Task,

    [string]$Workspace = '',

    [switch]$NoFeishuSync,

    [switch]$DryRun,

    [ValidateSet('Interactive', 'Batch')]
    [string]$Mode = 'Interactive',

    [string]$CodexModel = 'gpt-5.5',

    [ValidateSet('', 'low', 'medium', 'high', 'xhigh', 'max')]
    [string]$ReasoningEffort = 'high',

    [ValidateSet('', 'Auto', 'AutoReview', 'WorkspaceNever', 'ReadOnly', 'FullAccess')]
    [string]$PermissionMode = 'AutoReview'
)

$ErrorActionPreference = 'Stop'
$HarnessRoot = Split-Path -Parent $PSScriptRoot
$BundledPython = Join-Path $HarnessRoot '.venv\Scripts\python.exe'

if (Test-Path -LiteralPath $BundledPython) {
    $Python = $BundledPython
} else {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        throw 'Python was not found. Install Python 3.11+ or run scripts\install.ps1.'
    }
    $Python = $PythonCommand.Source
}

$CodexCommand = Get-Command codex -ErrorAction SilentlyContinue
if ($null -eq $CodexCommand) {
    throw 'Codex CLI was not found on PATH.'
}

if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = $HarnessRoot
}
$Workspace = [System.IO.Path]::GetFullPath($Workspace)
if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) {
    throw "Authorized workspace does not exist: $Workspace"
}

$PermissionDescription = switch ($PermissionMode) {
    'AutoReview' { 'workspace-write / on-request / auto-review' }
    'Auto' { 'workspace-write / on-request / user review' }
    'WorkspaceNever' { 'workspace-write / never ask' }
    'ReadOnly' { 'read-only / on-request' }
    'FullAccess' { 'danger-full-access / no approvals' }
    default { 'Codex current configuration' }
}

$Prompt = @"
You are GPT/Codex, the only orchestrator for an authorized CTF or SRC task.
Harness root: $HarnessRoot
Authorized workspace: $Workspace
Task: $Task

Follow $HarnessRoot\..\AGENTS.md. First inspect the task and local evidence. If
the task is simple, complete it yourself and invoke no peer. Only when a real
specialty/evidence gap or high-risk independent review justifies it, call one
bounded specialist with:
  & '$Python' -m collab_demo delegate __RUN_ID__ <agent> '<bounded question>'
Use at most two specialists, sequentially, and verify their claims yourself.
Record decision-useful progress with:
  & '$Python' -m collab_demo note __RUN_ID__ '<verified progress>'
Never expose credentials, cookies, private chain-of-thought, or unauthorized
target data. Return a concise final report with evidence and remaining
uncertainty.
"@

if ($DryRun) {
    [PSCustomObject]@{
        Codex = $CodexCommand.Source
        HarnessRoot = $HarnessRoot
        Workspace = $Workspace
        Task = $Task
        Mode = $Mode
        Model = if ([string]::IsNullOrWhiteSpace($CodexModel)) { '(current config)' } else { $CodexModel }
        ReasoningEffort = if ([string]::IsNullOrWhiteSpace($ReasoningEffort)) { '(current config)' } else { $ReasoningEffort }
        Permissions = $PermissionDescription
        Note = 'No run was created and no model was called.'
    } | ConvertTo-Json
    exit 0
}

$StartArguments = @('-m', 'collab_demo', 'start', $Task, '--workspace', $Workspace)
if ($NoFeishuSync) {
    $StartArguments += '--no-sync'
}

Push-Location $HarnessRoot
try {
    $StartOutput = & $Python @StartArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to create collaboration run.'
    }
    $Start = $StartOutput | ConvertFrom-Json
    $RunId = $Start.run_id
    $Prompt = $Prompt.Replace('__RUN_ID__', $RunId)

    $SelectedModel = if ([string]::IsNullOrWhiteSpace($CodexModel)) { 'current config' } else { $CodexModel }
    $SelectedEffort = if ([string]::IsNullOrWhiteSpace($ReasoningEffort)) { 'current config' } else { $ReasoningEffort }
    $LaunchNoteArguments = @(
        '-m', 'collab_demo', 'note', $RunId,
        "Codex launch settings: mode=$Mode; model=$SelectedModel; effort=$SelectedEffort; permissions=$PermissionDescription",
        '--kind', 'orchestrator.launch_config'
    )
    if ($NoFeishuSync) {
        $LaunchNoteArguments += '--no-sync'
    }
    & $Python @LaunchNoteArguments | Out-Null

    Write-Host "Collaboration run: $RunId"
    if ($Mode -eq 'Interactive') {
        $SummaryPath = Join-Path $HarnessRoot ".collab\runs\$RunId\final.md"
        $FinishCommand = "& '$Python' -m collab_demo finish $RunId --summary-file '$SummaryPath'"
        if ($NoFeishuSync) {
            $FinishCommand += ' --no-sync'
        }
        $Prompt += @"

This is a persistent interactive Codex session. The user can send follow-up
messages, correct paths, answer approval prompts, or stop and resume later. If
one command or tool fails, preserve the run, explain the concrete failure, and
continue with safe alternatives or wait for user guidance when it is genuinely
needed. Do not mark the run failed merely because one execution path failed.

When the task is genuinely complete, save the final report as UTF-8 text to:
  $SummaryPath
Then record completion with:
  $FinishCommand
If the user pauses or exits before completion, leave the run active so the same
Codex session can be resumed from the launcher.
"@
        $CodexArguments = @()
        if (-not [string]::IsNullOrWhiteSpace($CodexModel)) {
            $CodexArguments += @('--model', $CodexModel)
        }
        if (-not [string]::IsNullOrWhiteSpace($ReasoningEffort)) {
            $CodexArguments += @('-c', ('model_reasoning_effort="{0}"' -f $ReasoningEffort))
        }
        switch ($PermissionMode) {
            'AutoReview' {
                $CodexArguments += @(
                    '--sandbox', 'workspace-write',
                    '--ask-for-approval', 'on-request',
                    '-c', 'approvals_reviewer="auto_review"'
                )
            }
            'Auto' {
                $CodexArguments += @('--sandbox', 'workspace-write', '--ask-for-approval', 'on-request')
            }
            'WorkspaceNever' {
                $CodexArguments += @('--sandbox', 'workspace-write', '--ask-for-approval', 'never')
            }
            'ReadOnly' {
                $CodexArguments += @('--sandbox', 'read-only', '--ask-for-approval', 'on-request')
            }
            'FullAccess' {
                $CodexArguments += '--dangerously-bypass-approvals-and-sandbox'
            }
        }
        $CodexArguments += @('--no-alt-screen', '--cd', $HarnessRoot)
        if ($Workspace -ne $HarnessRoot) {
            $CodexArguments += @('--add-dir', $Workspace)
        }
        $CodexArguments += $Prompt

        Write-Host "启动配置：$SelectedModel / $SelectedEffort / $PermissionDescription" -ForegroundColor DarkCyan
        Write-Host '已进入交互式 Codex：可直接输入补充信息或纠正错误。' -ForegroundColor Cyan
        Write-Host '退出后可在主菜单选择 [9] 恢复最近一次对话。' -ForegroundColor DarkCyan
        & $CodexCommand.Source @CodexArguments
        $CodexExitCode = $LASTEXITCODE
        if ($CodexExitCode -ne 0) {
            $InterruptedArguments = @(
                '-m', 'collab_demo', 'note', $RunId,
                "Interactive Codex exited with code $CodexExitCode; run kept active for recovery.",
                '--kind', 'orchestrator.interrupted'
            )
            if ($NoFeishuSync) {
                $InterruptedArguments += '--no-sync'
            }
            & $Python @InterruptedArguments | Out-Null
            Write-Host "Codex 已退出（code $CodexExitCode），run 保持 active，可恢复后继续。" -ForegroundColor Yellow
        }
        $StateOutput = & $Python -m collab_demo show $RunId
        $State = $StateOutput | ConvertFrom-Json
        [PSCustomObject]@{
            run_id = $RunId
            status = $State.state.status
            brief = $Start.brief
            document_id = $Start.document_id
            recovery = '.\START_COLLAB.cmd resume'
        } | ConvertTo-Json -Depth 5
        exit 0
    }

    $Prompt += @"

This is a non-interactive batch run. Do not call the finish command; the
launcher records your final message after Codex exits.
"@
    $CodexArguments = @(
        'exec',
        '--ephemeral',
        '--sandbox', 'workspace-write',
        '--skip-git-repo-check',
        '--color', 'never',
        '--cd', $HarnessRoot
    )
    if ($Workspace -ne $HarnessRoot) {
        $CodexArguments += @('--add-dir', $Workspace)
    }
    $CodexArguments += '-'

    $FinalMessage = $Prompt | & $CodexCommand.Source @CodexArguments
    if ($LASTEXITCODE -ne 0) {
        $FailureArguments = @(
            '-m', 'collab_demo', 'fail', $RunId,
            'codex exec failed before completion'
        )
        if ($NoFeishuSync) {
            $FailureArguments += '--no-sync'
        }
        & $Python @FailureArguments | Out-Null
        throw "codex exec failed for run $RunId"
    }
    $FinalText = ($FinalMessage -join [Environment]::NewLine).Trim()
    if ([string]::IsNullOrWhiteSpace($FinalText)) {
        throw "codex exec returned an empty final message for run $RunId"
    }

    $FinishArguments = @(
        '-m', 'collab_demo', 'finish', $RunId, '--summary', $FinalText
    )
    if ($NoFeishuSync) {
        $FinishArguments += '--no-sync'
    }
    & $Python @FinishArguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Codex completed, but final event recording failed for run $RunId"
    }

    [PSCustomObject]@{
        run_id = $RunId
        route = $Start.route
        final = $FinalText
        brief = $Start.brief
        document_id = $Start.document_id
    } | ConvertTo-Json -Depth 8
} finally {
    Pop-Location
}
