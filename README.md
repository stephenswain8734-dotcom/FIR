# TFR Builder

This folder converts the FAA Temporary Flight Restriction feed into KML and KMZ files.

## Files

- `tfr_kml_converter.py` converts FAA GeoJSON, GeoJSON, KML, or KMZ sources into `active_tfrs.kml` and `active_tfrs.kmz`.
- `run_tfr_builder.ps1` is the hourly automation entry point.
- `serve_tfr_output.ps1` starts a local web server for the generated KML/KMZ files.
- `output/active_tfrs.kml` and `output/active_tfrs.kmz` are regenerated each run.
- `output/Active TFRs Google Earth Network Link.kml` can be opened in Google Earth as an auto-refreshing map named `Active TFRs`.
- `output/Active TFRs Local URL Network Link.kml` points Google Earth at the local live URL.

## Run Manually

From this folder:

```powershell
.\run_tfr_builder.ps1
```

The script accepts the browser-facing FAA URL, `https://tfr.faa.gov/tfr3/export/json`, and automatically uses the underlying FAA GeoServer JSON feed.

## Google Earth

Open `output/Active TFRs Google Earth Network Link.kml` in Google Earth. It points to the refreshed local `active_tfrs.kml` file and asks Google Earth to refresh once per hour.

To use a local URL instead of a direct file path, start the local server:

```powershell
.\serve_tfr_output.ps1
```

Then open `output/Active TFRs Local URL Network Link.kml` in Google Earth. It points to:

```text
http://127.0.0.1:8787/active_tfrs.kml
```

Keep the server window open while you want Google Earth to refresh from the URL.

## Public GitHub Pages URL

The root workflow `.github/workflows/update-tfr.yml` can publish `active_tfrs.kml`, `active_tfrs.kmz`, and a hosted Google Earth Network Link to GitHub Pages every hour. See `docs/github-pages-hosting.md`.
