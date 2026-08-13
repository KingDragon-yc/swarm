param(
    [ValidateSet('menu', 'doctor', 'demo', 'task', 'batch', 'resume', 'dry-run', 'show', 'chats', 'new-doc', 'manual')]
    [string]$Action = 'menu',

    [string]$Task = '',

    [string]$Workspace = '',

    [string]$CodexModel = '',

    [string]$ReasoningEffort = '',

    [string]$PermissionMode = '',

    [switch]$NoFeishuSync
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$HarnessRoot = Split-Path -Parent $PSScriptRoot
$ProjectRoot = Split-Path -Parent $HarnessRoot
$CollabScript = Join-Path $PSScriptRoot 'collab.ps1'
$OrchestrateScript = Join-Path $PSScriptRoot 'orchestrate.ps1'
$ManualPath = Join-Path $ProjectRoot 'USER_MANUAL.md'

function Show-Banner {
    Write-Host ''
    Write-Host '==================================================' -ForegroundColor DarkCyan
    Write-Host ' Buzz CTF / SRC · Codex 多模型协作入口' -ForegroundColor Cyan
    Write-Host '==================================================' -ForegroundColor DarkCyan
}

function Invoke-Doctor {
    & $CollabScript doctor --probe-cursor
    if ($LASTEXITCODE -ne 0) {
        throw 'Environment doctor failed.'
    }
}

function Invoke-Demo {
    & $CollabScript demo
    if ($LASTEXITCODE -ne 0) {
        throw 'Offline demo failed.'
    }
}

function Find-WorkspaceHint {
    $match = [regex]::Match($script:Task, '(?i)([a-z]:\\.+?)\s*$')
    if (-not $match.Success) {
        return ''
    }
    $candidate = $match.Groups[1].Value.Trim().Trim('"').Trim("'")
    if (Test-Path -LiteralPath $candidate -PathType Container) {
        return [System.IO.Path]::GetFullPath($candidate)
    }
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        return Split-Path -Parent ([System.IO.Path]::GetFullPath($candidate))
    }
    return ''
}

function Resolve-TaskInput {
    if ([string]::IsNullOrWhiteSpace($script:Task)) {
        $script:Task = Read-Host '请输入 CTF/SRC 任务'
    }
    if ([string]::IsNullOrWhiteSpace($script:Task)) {
        throw 'Task cannot be empty.'
    }
    if ([string]::IsNullOrWhiteSpace($script:Workspace)) {
        $hint = Find-WorkspaceHint
        $defaultWorkspace = if ([string]::IsNullOrWhiteSpace($hint)) {
            $ProjectRoot
        } else {
            Write-Host "已从任务中识别题目目录：$hint" -ForegroundColor DarkCyan
            $hint
        }
        $entered = Read-Host "授权工作区（直接回车使用 $defaultWorkspace）"
        $script:Workspace = if ([string]::IsNullOrWhiteSpace($entered)) {
            $defaultWorkspace
        } else {
            $entered
        }
    }
}

function Get-CodexModelCatalog {
    $CodexCommand = Get-Command codex -ErrorAction SilentlyContinue
    if ($null -eq $CodexCommand) {
        throw 'Codex CLI was not found on PATH.'
    }
    try {
        $raw = & $CodexCommand.Source debug models --bundled 2>$null
        if ($LASTEXITCODE -eq 0) {
            $catalog = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
            $models = @($catalog.models | Where-Object { $_.visibility -ne 'hide' })
            if ($models.Count -gt 0) {
                return $models
            }
        }
    } catch {
        # Fall through to a small catalog known by this launcher version.
    }
    return @(
        [PSCustomObject]@{ slug = 'gpt-5.5'; display_name = 'GPT-5.5'; supported_reasoning_levels = @([PSCustomObject]@{ effort = 'high' }, [PSCustomObject]@{ effort = 'medium' }, [PSCustomObject]@{ effort = 'xhigh' }, [PSCustomObject]@{ effort = 'low' }) },
        [PSCustomObject]@{ slug = 'gpt-5.4'; display_name = 'GPT-5.4'; supported_reasoning_levels = @([PSCustomObject]@{ effort = 'high' }, [PSCustomObject]@{ effort = 'medium' }, [PSCustomObject]@{ effort = 'xhigh' }, [PSCustomObject]@{ effort = 'low' }) },
        [PSCustomObject]@{ slug = 'gpt-5.6-terra'; display_name = 'GPT-5.6-Terra'; supported_reasoning_levels = @([PSCustomObject]@{ effort = 'high' }, [PSCustomObject]@{ effort = 'medium' }, [PSCustomObject]@{ effort = 'low' }) },
        [PSCustomObject]@{ slug = 'gpt-5.6-sol'; display_name = 'GPT-5.6-Sol'; supported_reasoning_levels = @([PSCustomObject]@{ effort = 'high' }, [PSCustomObject]@{ effort = 'medium' }, [PSCustomObject]@{ effort = 'low' }) }
    )
}

function Select-CodexLaunchSettings {
    if ([string]::IsNullOrWhiteSpace($script:CodexModel)) {
        $models = @(Get-CodexModelCatalog)
        $ordered = @($models | Where-Object { $_.slug -eq 'gpt-5.5' })
        $ordered += @($models | Where-Object { $_.slug -ne 'gpt-5.5' })

        Write-Host ''
        Write-Host '选择 Codex 模型' -ForegroundColor Cyan
        for ($i = 0; $i -lt $ordered.Count; $i++) {
            $suffix = if ($ordered[$i].slug -eq 'gpt-5.5') {
                '（推荐：当前 CTF/SRC 兼容性更好）'
            } else {
                ''
            }
            Write-Host ("[{0}] {1}  {2}{3}" -f ($i + 1), $ordered[$i].display_name, $ordered[$i].slug, $suffix)
        }
        Write-Host '[0] 使用 Codex 当前配置'
        Write-Host '[C] 手工输入模型 ID'
        $choice = Read-Host '请选择（直接回车使用 1）'
        if ([string]::IsNullOrWhiteSpace($choice)) {
            $choice = '1'
        }
        if ($choice -match '^[cC]$') {
            $script:CodexModel = (Read-Host '模型 ID').Trim()
            if ([string]::IsNullOrWhiteSpace($script:CodexModel)) {
                throw 'Model ID cannot be empty.'
            }
            $supportedEfforts = @('high', 'medium', 'xhigh', 'low', 'max')
        } elseif ($choice -eq '0') {
            $script:CodexModel = ''
            $script:ReasoningEffort = ''
            $supportedEfforts = @()
        } else {
            $selectedIndex = 0
            if (-not [int]::TryParse($choice, [ref]$selectedIndex) -or $selectedIndex -lt 1 -or $selectedIndex -gt $ordered.Count) {
                throw "Unknown model choice: $choice"
            }
            $selectedModel = $ordered[$selectedIndex - 1]
            $script:CodexModel = $selectedModel.slug
            $supportedEfforts = @($selectedModel.supported_reasoning_levels | ForEach-Object { $_.effort } | Where-Object { $_ -ne 'ultra' })
        }

        if (-not [string]::IsNullOrWhiteSpace($script:CodexModel) -and [string]::IsNullOrWhiteSpace($script:ReasoningEffort)) {
            $effortOrder = @('high', 'medium', 'xhigh', 'low', 'max')
            $efforts = @($effortOrder | Where-Object { $_ -in $supportedEfforts })
            $efforts += @($supportedEfforts | Where-Object { $_ -notin $efforts })
            Write-Host ''
            Write-Host '选择推理强度（Ultra 会自动派生子代理，与本项目稀疏委派协议冲突，因此不列出）' -ForegroundColor Cyan
            for ($i = 0; $i -lt $efforts.Count; $i++) {
                $suffix = if ($efforts[$i] -eq 'high') { '（推荐）' } else { '' }
                Write-Host ("[{0}] {1}{2}" -f ($i + 1), $efforts[$i], $suffix)
            }
            $effortChoice = Read-Host '请选择（直接回车使用 1）'
            if ([string]::IsNullOrWhiteSpace($effortChoice)) {
                $effortChoice = '1'
            }
            $effortIndex = 0
            if (-not [int]::TryParse($effortChoice, [ref]$effortIndex) -or $effortIndex -lt 1 -or $effortIndex -gt $efforts.Count) {
                throw "Unknown reasoning effort choice: $effortChoice"
            }
            $script:ReasoningEffort = $efforts[$effortIndex - 1]
        }
    }

    if ([string]::IsNullOrWhiteSpace($script:PermissionMode)) {
        Write-Host ''
        Write-Host '选择本地权限' -ForegroundColor Cyan
        Write-Host '[1] 工作区写入 + 自动审批复核（推荐，边界仍保留）'
        Write-Host '[2] 工作区写入 + 人工审批'
        Write-Host '[3] 工作区写入 + 永不询问（越界操作直接失败）'
        Write-Host '[4] 只读 + 人工审批'
        Write-Host '[5] 完全访问 + 无审批（高风险，仅用于隔离且可信的本地题目）' -ForegroundColor Yellow
        $permissionChoice = Read-Host '请选择（直接回车使用 1）'
        if ([string]::IsNullOrWhiteSpace($permissionChoice)) {
            $permissionChoice = '1'
        }
        $script:PermissionMode = switch ($permissionChoice) {
            '1' { 'AutoReview' }
            '2' { 'Auto' }
            '3' { 'WorkspaceNever' }
            '4' { 'ReadOnly' }
            '5' {
                $confirmation = Read-Host '完全访问会关闭 Codex 沙箱和审批；输入 FULL 确认'
                if ($confirmation -cne 'FULL') {
                    throw 'Full access was not confirmed.'
                }
                'FullAccess'
            }
            default { throw "Unknown permission choice: $permissionChoice" }
        }
    }
}

function Invoke-TaskRun(
    [switch]$DryRun,
    [ValidateSet('Interactive', 'Batch')]
    [string]$Mode = 'Interactive'
) {
    Resolve-TaskInput
    if ($Mode -eq 'Interactive') {
        Select-CodexLaunchSettings
    }
    $parameters = @{
        Task = $script:Task
        Workspace = $script:Workspace
        Mode = $Mode
        CodexModel = $script:CodexModel
        ReasoningEffort = $script:ReasoningEffort
        PermissionMode = $script:PermissionMode
    }
    if ($script:NoFeishuSync) {
        $parameters.NoFeishuSync = $true
    }
    if ($DryRun) {
        $parameters.DryRun = $true
    }
    & $OrchestrateScript @parameters
    if ($LASTEXITCODE -ne 0) {
        throw 'Codex orchestration failed.'
    }
}

function Resume-CodexSession {
    $CodexCommand = Get-Command codex -ErrorAction SilentlyContinue
    if ($null -eq $CodexCommand) {
        throw 'Codex CLI was not found on PATH.'
    }
    Push-Location $HarnessRoot
    try {
        & $CodexCommand.Source resume --last --no-alt-screen
        if ($LASTEXITCODE -ne 0) {
            throw 'No resumable Buzz Codex session was found, or resume failed.'
        }
    } finally {
        Pop-Location
    }
}

function Show-LatestRun {
    & $CollabScript show latest
    if ($LASTEXITCODE -ne 0) {
        throw 'No collaboration run is available.'
    }
}

function Open-Manual {
    if (-not (Test-Path -LiteralPath $ManualPath)) {
        throw "Manual not found: $ManualPath"
    }
    Start-Process -FilePath 'notepad.exe' -ArgumentList @($ManualPath)
    Write-Host "已打开：$ManualPath"
}

function Show-FeishuChats {
    & $CollabScript feishu-chats
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to list Feishu chats.'
    }
}

function New-FeishuDocument {
    $title = $script:Task
    if ([string]::IsNullOrWhiteSpace($title)) {
        $title = Read-Host '新文档标题（直接回车使用默认标题）'
    }
    $arguments = @('feishu-create-doc')
    if (-not [string]::IsNullOrWhiteSpace($title)) {
        $arguments += @('--title', $title)
    }
    & $CollabScript @arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to create Feishu document.'
    }
}

function Invoke-Menu {
    Show-Banner
    Write-Host '[1] 环境自检（推荐首次运行）'
    Write-Host '[2] 离线零委派 Demo'
    Write-Host '[3] 启动交互式 Codex 总控任务（推荐，可中途纠偏）'
    Write-Host '[4] 预检任务参数（不调用模型）'
    Write-Host '[5] 查看最近一次运行记录'
    Write-Host '[6] 打开 USER_MANUAL.md'
    Write-Host '[7] 列出机器人所在群 chat_id'
    Write-Host '[8] 创建测试飞书文档并显示 document_id'
    Write-Host '[9] 恢复最近一次交互式 Codex 对话'
    Write-Host '[B] 启动无人值守批处理（codex exec）'
    Write-Host '[0] 退出'
    Write-Host ''
    $choice = Read-Host '请选择'
    switch ($choice) {
        '1' { Invoke-Doctor }
        '2' { Invoke-Demo }
        '3' { Invoke-TaskRun }
        '4' { Invoke-TaskRun -DryRun }
        '5' { Show-LatestRun }
        '6' { Open-Manual }
        '7' { Show-FeishuChats }
        '8' { New-FeishuDocument }
        '9' { Resume-CodexSession }
        'b' { Invoke-TaskRun -Mode Batch }
        'B' { Invoke-TaskRun -Mode Batch }
        '0' { return }
        default { throw "Unknown menu choice: $choice" }
    }
}

Set-Location $ProjectRoot
try {
    switch ($Action) {
        'menu' { Invoke-Menu }
        'doctor' { Invoke-Doctor }
        'demo' { Invoke-Demo }
        'task' { Invoke-TaskRun }
        'batch' { Invoke-TaskRun -Mode Batch }
        'resume' { Resume-CodexSession }
        'dry-run' { Invoke-TaskRun -DryRun }
        'show' { Show-LatestRun }
        'chats' { Show-FeishuChats }
        'new-doc' { New-FeishuDocument }
        'manual' { Open-Manual }
    }
} catch {
    Write-Host ("启动失败：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
