param(
    [string]$Code,
    [string]$MdbPath = "C:\Users\zfm11\Desktop\db3.mdb",
    [switch]$IncludeProduct
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

# Read explicitly named product fields; do not infer from product names.
function Get-ProductField($Row, [string[]]$Names) {
    foreach ($name in $Names) {
        if ($Row.Table.Columns.Contains($name) -and -not $Row.IsNull($name)) {
            $value = ([string]$Row[$name]).Trim()
            if ($value) { return $value }
        }
    }
    return ""
}

function Read-ProductTable($Connection, [string]$Sql, $Key) {
    $query = $Connection.CreateCommand()
    $adapter2 = $null
    try {
        $query.CommandText = $Sql
        if ($null -ne $Key) { [void]$query.Parameters.AddWithValue("?", $Key) }
        $table = New-Object System.Data.DataTable
        $adapter2 = New-Object System.Data.OleDb.OleDbDataAdapter($query)
        [void]$adapter2.Fill($table)
        return ,$table
    } finally {
        if ($null -ne $adapter2) { $adapter2.Dispose() }
        $query.Dispose()
    }
}

function Get-ProductDetails($Connection, $ProductCode) {
    $details = @{ maker = ""; sku = "" }
    if ($null -eq $ProductCode -or $ProductCode -is [DBNull] -or ([string]$ProductCode).Trim() -eq "") { return $details }
    try {
        $products = Read-ProductTable $Connection 'SELECT TOP 2 * FROM [M_商品マスタ] WHERE [商品コード] = ?' $ProductCode
        if ($products.Rows.Count -ne 1) { return $details }
        $p = $products.Rows[0]
        $details.sku = Get-ProductField $p @("メーカー品番", "メーカ品番", "メーカー型番", "メーカ型番", "品番")
        $details.maker = Get-ProductField $p @("メーカー名", "メーカ名", "メーカー名称", "メーカ名称", "製造メーカー名")
        if (-not $details.maker) {
            $keys = @("メーカーコード", "メーカコード", "メーカーCD", "メーカCD", "メーカーID", "メーカID")
            $productKey = $keys | Where-Object { $p.Table.Columns.Contains($_) -and -not $p.IsNull($_) -and ([string]$p[$_]).Trim() } | Select-Object -First 1
            if ($productKey) {
                $code = $p[$productKey]
                # Resolve only through explicitly named manufacturer masters.
                $names = @()
                foreach ($tableName in @("M_メーカーマスタ", "M_メーカマスタ", "M_メーカー", "M_メーカ")) {
                    try {
                        $schema = Read-ProductTable $Connection "SELECT * FROM [$tableName] WHERE 1=0" $null
                        $keyName = $productKey
                        if (-not $schema.Columns.Contains($keyName)) {
                            $keyName = $keys | Where-Object { $schema.Columns.Contains($_) } | Select-Object -First 1
                        }
                        if (-not $keyName) { continue }
                        $makerRows = Read-ProductTable $Connection "SELECT TOP 2 * FROM [$tableName] WHERE [$keyName] = ?" $code
                        if ($makerRows.Rows.Count -eq 1) {
                            $name = Get-ProductField ($makerRows.Rows[0]) @("メーカー名", "メーカ名", "メーカー名称", "メーカ名称", "名称", "メーカー", "メーカ")
                            if ($name) { $names += $name }
                        }
                    } catch { continue }
                }
                $unique = @($names | Select-Object -Unique)
                if ($unique.Count -eq 1) { $details.maker = $unique[0] }
            } else {
                # Avoid filling a generic manufacturer field with a code-like value.
                $name = Get-ProductField $p @("メーカー", "メーカ")
                if ($name -and $name -notmatch '^[A-Za-z0-9_\- ./]+$') { $details.maker = $name }
            }
        }
    } catch {
        # Optional enrichment must never break the existing barcode lookup.
    }
    return $details
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
    s.商品コード AS LookupProductCode,
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
        if ($IncludeProduct) {
            $details = Get-ProductDetails $conn ($r["LookupProductCode"])
            $res.itemName = "" + $r["商品名"]
            $res.maker = $details.maker
            $res.sku = $details.sku
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
