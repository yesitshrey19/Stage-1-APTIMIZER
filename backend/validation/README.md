# Aptimizer Site validation

Runs your test plots through the same engines the web app uses (GIS site analysis,
development controls and site layout), then writes an Excel workbook. In that workbook
you type in the values you measure yourself, and each one is marked PASS or FAIL against
a stated tolerance.

## 1. Trace each plot's boundary (recommended)

1. Open **Google Earth Pro** (free desktop app) and find the plot.
2. Click **Add → Polygon**, name it with the plot ID (for example `P03 Nexus Koramangala`),
   and click around the boundary.
3. Right-click the polygon, choose **Save Place As…**, and pick **KML** (not KMZ).
4. Put all the files in one folder (`P01.kml`, `P02.kml`, …). Alternatively, save one KML
   containing every polygon, as long as each name starts with its plot ID.

Plots without a KML are modelled as a square of the recorded area. Their location checks
are still valid, but their area and layout results are not.

## 1b. (Recommended) Add measured road widths

Add a column titled **Measured road width (m)** to `Plot_Master` and fill it with the kerb-to-kerb
width of the widest road each plot faces (Google Earth: Tools → Ruler → Line). The layout test
uses it; where it is blank, a 12 m road is assumed.

## 2. Run

From `Aptimizer-Stage1/backend`:

```bash
../../Apt--main/backend/.venv/bin/python -m validation.run_validation --input "$HOME/Desktop/GIS_Testing_Base_P01_updated (1).xlsx" --kml "$HOME/Desktop/plot_boundaries"
```

The workbook is written next to the input file as `…_Aptimizer_Validation.xlsx`.
Map data is cached for 7 days, so re-runs take about a second per plot.

## 3. Fill in the yellow cells

On the **Comparison** sheet, type each measured value into its yellow "Measured (you)"
cell. The **Method** sheet explains how to measure each one (Google Earth ruler and
elevation, the NOAA Solar Calculator, the Global Solar Atlas, IS 1893 and IS 875-3).
The Error and Result columns, and the **Summary** sheet, update automatically.

For the suitability check, ask an expert to rank the plots **without** seeing Aptimizer's
scores. Then enter their ranks under "Expert rank (you)".

## 4. Re-run without losing what you typed

```bash
../../Apt--main/backend/.venv/bin/python -m validation.run_validation --input "$HOME/Desktop/GIS_Testing_Base_P01_updated (1).xlsx" --kml "$HOME/Desktop/plot_boundaries" --merge "$HOME/Desktop/GIS_Testing_Base_P01_updated (1)_Aptimizer_Validation.xlsx" --output "$HOME/Desktop/Aptimizer_Validation_v2.xlsx"
```

`--merge` copies every measured value and expert rank from the earlier workbook into the
new one, matched by plot ID.
