# Font licences

Auto-edit can use any family of the Google Fonts catalogue (`catalog.json` is a snapshot of
https://fonts.google.com/metadata/fonts: family, category, scripts, weights, popularity; refresh it with `fonts.refresh_catalog()`).
Every family on Google Fonts is distributed under an open licence: the SIL Open Font License 1.1 (the large majority), the
Apache License 2.0 or the Ubuntu Font Licence. All three allow use in videos and redistribution of the unmodified font file.
Fonts are downloaded on first use from fonts.gstatic.com into a cache directory (`AUTO_EDIT_FONT_DIR`, default `backend/data/fonts`) and are
never committed to this repository. The only fonts shipped in the repository are Poppins (OFL, see `OFL-Poppins.txt`) as the fallback.
Each family's own licence text is in the google/fonts repository under `ofl/<family>/OFL.txt` (or `apache/`, `ufl/`).
