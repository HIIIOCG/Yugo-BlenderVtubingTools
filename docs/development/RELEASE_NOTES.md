# Public release-writing rules

These rules apply to the public GitHub Release body for Yugo and Yugu Node
Studio. Write for someone choosing a download and checking what changed.
The integration role prepares and reviews the body as part of the existing
release task; this document does not authorize publication.

## Required body

Write the entire body in English, in this order:

1. `## Downloads`, followed by a compact `Download` / `Purpose` table of direct
   package download links. Put the main package first and label actual optional
   packages as optional. State a platform restriction beside an optional package
   when it differs from the main package. Keep the purpose to a short phrase.
2. `## What's changed in <version>`, followed by one to six short, single-sentence
   bullets of at most 40 words each. Use `- **Actual node or feature label** —
   Concrete change and what the user can do with it.`
3. A final, single `**Compatibility:**` line with the supported Blender versions
   and relevant, verified platform or workflow restrictions. State only
   compatibility supported by the candidate's metadata and qualification evidence.

The download table contains only the packages a user can install. Yugo currently
has two links: the main Yugo ZIP and the optional ProtoMotions / PhysX Tracker
ZIP. Studio currently has one link, its main Studio ZIP. Do not add a second
link when the product has no separately released optional package. Use the exact
versioned asset URLs for that release; do not link to `latest`, a release page,
or a source-code archive as the installer.

Use actual display labels from the released source when naming nodes. Each
bullet must explain a change in this version and its practical use, without
internal module names or vague claims such as "various improvements". Compare
the candidate with the previous released version and check the affected source
and validation evidence. Existing functionality is not a new feature: for
example, Yugo 0.2.3 extends existing physics nodes to Linux and Apple Silicon;
it does not introduce those nodes. An unchanged optional package belongs in
the download table, not in the list of changes.

Keep the body limited to these three parts. Do not add installation or upgrade
steps, an inventory or explanation of the Assets list, checksums, commit IDs,
build details, test results, qualification or provenance prose, dependency
internals, or links to those records. Do not hide that material in a collapsed
section. Installation help remains in the handbook and READMEs; engineering
evidence remains in the existing changelogs, qualification documents, Issues,
PRs, CI artifacts, checksum files, and build reports.

## Source files and validation

Store each public body separately from engineering records:

| Product | Public body path | Release tag |
| --- | --- | --- |
| Yugo | `docs/releases/YUGO_<version_with_underscores>_NOTES.md` | `v<version>` |
| Yugu Node Studio | `docs/releases/STUDIO_<version_with_underscores>_NOTES.md` | `studio-v<version>` |

Use [Yugo 0.2.3](../releases/YUGO_0_2_3_NOTES.md) and
[Studio 0.7.0](../releases/STUDIO_0_7_0_NOTES.md) as format examples. Read the
candidate's real changes and package metadata before writing a new version;
do not carry forward their feature claims or compatibility automatically.
The presence of a notes file does not mean its version has been published.

From the task workspace, run the appropriate command:

```text
python -B scripts/check_release_notes.py --product yugo
python -B scripts/check_release_notes.py --product studio
```

The checker selects the current product version, validates its dedicated body,
and prints the checked repository-relative notes path on success. Add
`--tag v<version>` for Yugo or `--tag studio-v<version>` for Studio to check the
release tag as well. Both publishing workflows must pass the tag and use the
returned file directly as the release body. They must not extract a changelog,
append engineering metadata, or generate another body after validation.

Review the factual accuracy, English, brevity, and real node labels as well as
running the checker. A format check cannot establish feature behavior or prove
platform compatibility. Keep those review and validation records outside the
public body.

## Published assets and body corrections

Preserve existing release tags and all published assets, including ZIPs,
checksums, and build reports. The concise body does not remove those records or
change their upload and validation requirements. A package correction requires
a new product version; never replace the bytes under an existing asset URL.

An authorized correction to a public release body may clarify its downloads
or wording while preserving the existing tag and every asset. Update the
corresponding dedicated notes file so future work retains the approved text.
Such a correction does not authorize a new tag, a rebuilt package, or a new
publication. Existing publication workflows continue to reject an already
published release.
