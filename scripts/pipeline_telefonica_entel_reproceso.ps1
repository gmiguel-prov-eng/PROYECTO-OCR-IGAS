# 2a pasada OCR PARCIAL+INCOMPLETO (Telefónica + Entel) → 06 → 07 aislado en extension/
# Uso: pasar la carpeta de salida (dentro de extension) como parametro o via config.
param(
    [string]$Config = "config/config_local.yaml",
    [string]$SalidaExtension = ""
)
$ErrorActionPreference = "Stop"

# Raiz del repo = carpeta padre de /scripts (sin rutas absolutas embebidas).
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$cfg = $Config
if ([string]::IsNullOrWhiteSpace($SalidaExtension)) {
    throw "Indica -SalidaExtension con la carpeta destino dentro de 'extension' (ruta de red del entorno)."
}
$ext = $SalidaExtension
New-Item -ItemType Directory -Force -Path $ext | Out-Null

Write-Host "=== 05 OCR ENTEL PERU (solo-parcial) ==="
python src/interfaces/cli/flujo_caja_oficios/run_05_ocr_fichas_oficios.py --config $cfg --solo-parcial --empresa "ENTEL PERU"
if ($LASTEXITCODE -ne 0) { throw "Fallo OCR ENTEL exit=$LASTEXITCODE" }

Write-Host "=== 05 OCR TELEFONICA DEL PERU (solo-parcial) ==="
python src/interfaces/cli/flujo_caja_oficios/run_05_ocr_fichas_oficios.py --config $cfg --solo-parcial --empresa "TELEFONICA DEL PERU"
if ($LASTEXITCODE -ne 0) { throw "Fallo OCR TELEFONICA exit=$LASTEXITCODE" }

Write-Host "=== 06 completar_solicitud ==="
python src/interfaces/cli/flujo_caja_oficios/run_06_completar_solicitud.py --config $cfg
if ($LASTEXITCODE -ne 0) { throw "Fallo 06 exit=$LASTEXITCODE" }

Write-Host "=== 07 unir (carpeta aislada telefonica_entel_reproceso) ==="
python src/interfaces/cli/flujo_caja_oficios/run_07_unir_solicitud_oficio.py `
  --config $cfg `
  --empresa "TELEFONICA DEL PERU,ENTEL PERU" `
  --salida-pdfs $ext `
  --reporte (Join-Path $ext "reporte_solicitud_oficio.csv")
if ($LASTEXITCODE -ne 0) { throw "Fallo 07 exit=$LASTEXITCODE" }

Write-Host "=== limpio entrega aislado ==="
python src/interfaces/cli/excepcionales/run_limpiar_resultado_final.py `
  --config $cfg `
  --tipo oficios `
  --entrada (Join-Path $ext "reporte_solicitud_oficio.csv") `
  --salida (Join-Path $ext "reporte_solicitud_oficio_limpio.csv")
if ($LASTEXITCODE -ne 0) { throw "Fallo limpio exit=$LASTEXITCODE" }

Write-Host "PIPELINE OK. Salida: $ext"
