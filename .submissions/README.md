# Upload receipts

Each file in this folder is a receipt for one city uploaded to the Green City Map website. The website writes the receipt when the upload arrives. `skill/pipeline/submit_city.py` saves it here as `<cc>-<city>-<date>-<sha256[:12]>.json`, and a pull request adds it. A receipt holds the city, the date, the bundle's checksum and size, the data release, the skill version and the optional credit. It holds nothing else, and never the bundle itself.

A pull request here must be one commit, on top of the latest main, that adds exactly one new receipt and changes nothing else. A GitHub check tests it with `skill/pipeline/check_receipt.py`. The check and the checker both come from the main branch, so a pull request can't change them. The check can also ask the website whether the upload is waiting for review. You can run the same file check yourself: `python3 -I skill/pipeline/check_receipt.py .submissions/<file>.json`.

The check only helps. The site owner checks the stored bundle against the receipt with the website's own tools, publishes the city, then squash-merges the pull request with a command pinned to the commit that was checked. Never edit a receipt by hand: any change makes the owner's check fail. How the whole flow works is in [docs/UPLOAD.md](../docs/UPLOAD.md).
