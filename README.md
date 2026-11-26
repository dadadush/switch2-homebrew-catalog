# Switch 2 Homebrew Catalog

A community homebrew catalog for Nintendo Switch 2, designed for [IZ2S Cheese](https://github.com/byizmysh/iz2s-cheese).

## Catalog URLs

In IZ2S Cheese, open **Settings → Catalog URL** and paste the address for your version.

**IZ2S Cheese 0.8.2 and later**

```text
https://raw.githubusercontent.com/dadadush/switch2-homebrew-catalog/main/catalog/catalog-new.tsv
```

**IZ2S Cheese 0.8.0–0.8.1**

```text
https://raw.githubusercontent.com/dadadush/switch2-homebrew-catalog/main/catalog/catalog.tsv
```

IZ2S Cheese downloads `artwork.bin` from the same folder for both catalogs. Press **Y → Refresh** in the catalog to get the latest list and thumbnails.

## Manual installs

- **Cemu for Switch 2.** Download `cemu-nx2.nro` from [NaGaa95's releases](https://github.com/NaGaa95/Cemu-nx/releases). The current upstream build exceeds Cheese's 128 MiB file limit. The catalog still offers the older build that fits.
- **IZPS2 on IZ2S.** Install the IZA edition from the catalog. For manual installation, import the App Library package from the [IZPS2 release page](https://github.com/byizmysh/IZPS2-emulator/releases/latest).
- **IZtwiNS2 on IZ2S.** The catalog installs the NRO build. To install the IZA edition, import the package from the [IZtwiNS2 release page](https://github.com/byizmysh/IZtwiNS2/releases/latest) through IZ2S App Library.

## Catalog updates

IZMYSH projects appear first in each category. Other apps are ordered by GitHub repository stars, with ties sorted by title.


The [Update catalogs workflow](.github/workflows/update-catalogs.yml) checks GitHub Releases every hour and can also run from the **Actions** tab. It uses the project and download filename already recorded for each build. Versioned filenames use the templates in [the update script](scripts/update_catalogs.py).

The workflow publishes the version, download URL, size, and SHA-256 together after checking the files and their package format. If a release is missing the expected build or an IZA companion, the workflow keeps the current entry. It uses published stable releases and skips versions it cannot safely order. Moader stays pinned because its resident module and PC backend need a matching release. Cheese itself stays at 0.8.1 in the legacy feed for compatibility with its older self-updater.

To preview updates locally, run `python3 scripts/update_catalogs.py --dry-run`. Set `GH_TOKEN` to use authenticated GitHub API requests.

## Files

- `catalog/catalog.tsv` — application details, download links and checksums in IZSTORE2 format.
- `catalog/catalog-new.tsv` — the IZSTORE3 catalog. It can group a single app's IZA and NRO builds under one entry.
- `catalog/artwork.bin` — application thumbnails.

Application files are downloaded from their developers' release pages.
