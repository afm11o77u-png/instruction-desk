param(
    [string]$Code,
    [string]$MdbPath = "C:\Users\zfm11\Desktop\db3.mdb"
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $Code) {
    @{ found = $false; error = "コードが指定されていません。" } | ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path $MdbPath)) {
    @{ found = $false; error = "Accessファイルが見つかりません: $MdbPath" } | ConvertTo-Json -Compress
    exit 0
}

# Clean code
$cleanCode = $Code -replace '[^0-9a-zA-Z_\-\/]', ''

$connStr = "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$MdbPath;Persist Security Info=False;"
$conn = New-Object System.Data.OleDb.OleDbConnection($connStr)
try {
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = @"
SELECT TOP 1
    s.MasterCode,
    c.顧客名,
    c.顧客コード,
    c.集配サイクル,
    c.集配曜日フラグ,
    p.商品名,
    p.ラベル表示用_商品名,
    s.サイズ,
    k.部署名,
    k.個人名
FROM ((([M_識別コードマスタ] s
LEFT JOIN [M_顧客マスタ] c ON s.顧客コード = c.顧客コード)
LEFT JOIN [M_商品マスタ] p ON s.商品コード = p.商品コード)
LEFT JOIN [M_契約マスタ] k ON s.KID = k.KID)
WHERE s.MasterCode = '$cleanCode'
"@
    $dt = New-Object System.Data.DataTable
    $adapter = New-Object System.Data.OleDb.OleDbDataAdapter($cmd)
    [void]$adapter.Fill($dt)
    if ($dt.Rows.Count -gt 0) {
        $r = $dt.Rows[0]
        $res = @{
            found = $true
            masterCode = "" + $r["MasterCode"]
            customer = "" + $r["顧客名"]
            customerCode = "" + $r["顧客コード"]
            cycle = "" + $r["集配サイクル"]
            cycleFlag = "" + $r["集配曜日フラグ"]
            itemName = if ($r["ラベル表示用_商品名"]) { "" + $r["ラベル表示用_商品名"] } elseif ($r["商品名"]) { "" + $r["商品名"] } else { "" }
            size = "" + $r["サイズ"]
            department = "" + $r["部署名"]
            person = "" + $r["個人名"]
        }
        $res | ConvertTo-Json -Compress
    } else {
        @{ found = $false } | ConvertTo-Json -Compress
    }
} catch {
    @{ found = $false; error = "" + $_.Exception.Message } | ConvertTo-Json -Compress
} finally {
    $conn.Close()
}
