# GitHub Pages Hosting For Active TFRs

This repo can publish the generated FAA Temporary Flight Restriction KML/KMZ files to GitHub Pages every hour.

## Files Published

The workflow publishes:

- `active_tfrs.kml`
- `active_tfrs.kmz`
- `Active_TFRs_Network_Link.kml`
- `index.html`

## Enable GitHub Pages

1. Open the GitHub repository.
2. Go to `Settings` -> `Pages`.
3. Under `Build and deployment`, set `Source` to `GitHub Actions`.
4. Save the setting.
5. Go to `Actions`.
6. Run `Update Active TFR KML` manually once, or wait for the next hourly run.

## Google Earth Pro

After the first successful workflow run, open this URL in Google Earth Pro:

```text
https://<github-username>.github.io/<repo-name>/Active_TFRs_Network_Link.kml
```

For the repo `stephenswain8734-dotcom/FIR`, the expected URL is:

```text
https://stephenswain8734-dotcom.github.io/FIR/Active_TFRs_Network_Link.kml
```

The Network Link points Google Earth Pro at:

```text
https://stephenswain8734-dotcom.github.io/FIR/active_tfrs.kml
```

The link asks Google Earth Pro to refresh every hour.

## Manual Refresh

To force a refresh:

1. Go to `Actions`.
2. Select `Update Active TFR KML`.
3. Choose `Run workflow`.

The site will update after the workflow completes.
