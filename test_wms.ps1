# WMS Phase 1 — Тестер с кнопками
# Запуск: powershell -ExecutionPolicy Bypass -File test_wms.ps1

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$url  = "http://it-mshenderov/1ctesterp5/hs/LikoRest/API"
$user = "administrator"
$pass = "224"
$pair = "${user}:${pass}"
$b64  = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))

$tests = @(
    @{n="01 CheckConnection";   b=@{ProcName="WMS_CheckConnection"}},
    @{n="02 GetWarehouses";     b=@{ProcName="WMS_GetWarehouses"}},
    @{n="03 GetRacks";          b=@{ProcName="WMS_GetRacks"}},
    @{n="04 GetOccupancy";      b=@{ProcName="WMS_GetOccupancy"}},
    @{n="05 GetFloor";          b=@{ProcName="WMS_GetFloor"}},
    @{n="06 FindCell";          b=@{ProcName="WMS_FindCell"}},
    @{n="07 ValidatePlacement"; b=@{ProcName="WMS_ValidatePlacement"}},
    @{n="08 MovePallet";        b=@{ProcName="WMS_MovePallet"}},
    @{n="09 ExportSnapshot";    b=@{ProcName="WMS_ExportSnapshot"}},
    @{n="10 PlacePallets";      b=@{ProcName="WMS_PlacePallets"}},
    @{n="11 GenerateMockData";  b=@{ProcName="WMS_GenerateMockData"}}
)

$form = New-Object System.Windows.Forms.Form
$form.Text = "WMS Phase 1 — Тестер"
$form.Size = New-Object System.Drawing.Size(800, 700)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Consolas", 10)

# URL / Auth row
$lblUrl = New-Object System.Windows.Forms.Label
$lblUrl.Text = "URL:"
$lblUrl.Location = New-Object System.Drawing.Point(10, 15)
$lblUrl.Size = New-Object System.Drawing.Size(50, 25)
$form.Controls.Add($lblUrl)

$txtUrl = New-Object System.Windows.Forms.TextBox
$txtUrl.Text = $url
$txtUrl.Location = New-Object System.Drawing.Point(60, 12)
$txtUrl.Size = New-Object System.Drawing.Size(520, 25)
$form.Controls.Add($txtUrl)

$lblLogin = New-Object System.Windows.Forms.Label
$lblLogin.Text = "Login:"
$lblLogin.Location = New-Object System.Drawing.Point(590, 15)
$lblLogin.Size = New-Object System.Drawing.Size(45, 25)
$form.Controls.Add($lblLogin)

$txtLogin = New-Object System.Windows.Forms.TextBox
$txtLogin.Text = $user
$txtLogin.Location = New-Object System.Drawing.Point(635, 12)
$txtLogin.Size = New-Object System.Drawing.Size(70, 25)
$form.Controls.Add($txtLogin)

$lblPass = New-Object System.Windows.Forms.Label
$lblPass.Text = "Pass:"
$lblPass.Location = New-Object System.Drawing.Point(710, 15)
$lblPass.Size = New-Object System.Drawing.Size(40, 25)
$form.Controls.Add($lblPass)

$txtPass = New-Object System.Windows.Forms.TextBox
$txtPass.Text = $pass
$txtPass.Location = New-Object System.Drawing.Point(750, 12)
$txtPass.Size = New-Object System.Drawing.Size(40, 25)
$txtPass.PasswordChar = '*'
$form.Controls.Add($txtPass)

# Buttons
$y = 45
$btnHeight = 32
foreach ($t in $tests) {
    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = $t.n
    $btn.Location = New-Object System.Drawing.Point(10, $y)
    $btn.Size = New-Object System.Drawing.Size(180, $btnHeight)
    $btn.Tag = $t.b
    $btn.Add_Click({
        $body = $this.Tag
        $name = $this.Text
        $txtResponse.Text = "Wait..."
        $auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($txtLogin.Text + ":" + $txtPass.Text))
        try {
            $json = $body | ConvertTo-Json -Depth 10 -Compress
            $result = Invoke-RestMethod -Uri $txtUrl.Text -Method Post -Body $json `
                -ContentType "application/json" `
                -Headers @{ Authorization = "Basic $auth" } `
                -TimeoutSec 120
            $txtResponse.Text = $result | ConvertTo-Json -Depth 10
        } catch {
            $txtResponse.Text = "ERROR: " + $_.Exception.Message
            if ($_.Exception.Response) {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $txtResponse.Text += "`r`n`r`n" + $reader.ReadToEnd()
            }
        }
    })
    $form.Controls.Add($btn)
    $y += $btnHeight + 3
}

# Response box
$txtResponse = New-Object System.Windows.Forms.TextBox
$txtResponse.Multiline = $true
$txtResponse.ScrollBars = "Both"
$txtResponse.Location = New-Object System.Drawing.Point(200, 45)
$txtResponse.Size = New-Object System.Drawing.Size(590, $($y - 45))
$txtResponse.Font = New-Object System.Drawing.Font("Consolas", 9)
$form.Controls.Add($txtResponse)

# Status
$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Text = "Готов"
$lblStatus.Location = New-Object System.Drawing.Point(10, $($y + 5))
$lblStatus.Size = New-Object System.Drawing.Size(780, 25)
$lblStatus.ForeColor = "Green"
$form.Controls.Add($lblStatus)

$form.ShowDialog() | Out-Null
