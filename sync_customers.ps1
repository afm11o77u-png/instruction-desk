param(
    [string]$MdbPath = "C:\Users\zfm11\Desktop\db3.mdb"
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Test-Path $MdbPath)) {
    Write-Error "Accessファイルが見つかりません: $MdbPath"
    exit 1
}

$connStr = "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$MdbPath;Persist Security Info=False;"
$conn = New-Object System.Data.OleDb.OleDbConnection($connStr)

try {
    $conn.Open()
    $cmd = $conn.CreateCommand()
    $cmd.CommandText = "SELECT 顧客コード, 顧客名, 集配サイクル, 集配曜日フラグ FROM [M_顧客マスタ] ORDER BY 顧客コード"
    $dt = New-Object System.Data.DataTable
    $adapter = New-Object System.Data.OleDb.OleDbDataAdapter($cmd)
    [void]$adapter.Fill($dt)
    
    $list = New-Object System.Collections.Generic.List[Object]
    foreach ($r in $dt.Rows) {
        $list.Add(@{
            code = "" + $r["顧客コード"]
            name = "" + $r["顧客名"]
            cycle = "" + $r["集配サイクル"]
            flag = "" + $r["集配曜日フラグ"]
        })
    }
    
    $list | ConvertTo-Json -Compress
} finally {
    $conn.Close()
}
