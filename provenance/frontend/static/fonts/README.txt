NotoSansSC-Medium-subset.woff
=============================

Noto Sans SC (Medium), subset. License: SIL Open Font License 1.1 (free to use,
modify and redistribute, including commercially). Source font: Noto Sans SC,
installed at C:/Windows/Fonts/NotoSansSC-Medium.otf on this machine.

Why self-host at all: the system font "Microsoft YaHei" is copyrighted by
Founder (北大方正). Microsoft only licensed it for display/printing inside
Windows -- exporting a layout that uses it into images or video for external
release is outside that licence.

Regenerate (needs fontTools in the project venv):

  .venv-live\Scripts\pyftsubset.exe "C:/Windows/Fonts/NotoSansSC-Medium.otf" ^
    --output-file="NotoSansSC-Medium-subset.woff" ^
    --flavor=woff ^
    --unicodes="U+0020-007E,U+00A0-00FF,U+2000-206F,U+20A0-20BF,U+2190-21FF,U+2460-24FF,U+25A0-25FF,U+2600-26FF,U+3000-303F,U+3040-30FF,U+3200-33FF,U+4E00-9FFF,U+FE10-FE1F,U+FE30-FE4F,U+FF00-FFEF" ^
    --layout-features="*" --no-hinting

Output is ~4.4 MB. Narrowing the CJK range to GB2312 (~2.5 MB) is possible but
then rare characters fall back to another font mid-sentence.
