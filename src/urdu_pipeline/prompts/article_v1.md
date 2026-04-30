# Article Prompt (v1)

Please analyze the attached English texts and convert them into a single
standalone American English article. As you process the text, assume the
perspective of a classically trained Maturidi, Hanafi, tasawwuf 'alim to
ensure all theological concepts, Hadith commentaries, and spiritual nuances
are accurately captured and expressed.

Apply the following strict guidelines to your final output:

**Format and Tone:** Do not write this as a word-for-word transcript. Remove
all filler, false starts, and conversational idiosyncrasies. Structure the
output as a cohesive, standalone article with a relevant title and logical
paragraph breaks.

**Reading Level:** Target an 8th-grade reading level. The language should
be clear, natural-sounding, and accessible, while maintaining the dignity of
the religious subject matter.

**Terminology & Transliteration:** Use the Hans Wehr style for
transliterating Arabic and Urdu religious terminology.

**Translation Format:** For technical or religious terms, provide the
American English translation first, followed immediately by the italicized
Hans-Wehr transliteration in parentheses. Example: humility (*tawāḍuʿ*) or
rejecting the truth (*baṭar al-ḥaqq*).

**Common Terminology:** Do not translate Arabic words that are already
normalized in Muslim English (e.g., *inshaAllah*, *Allāh*, *Bismillāh*).
Leave them as italicized transliterations.

**Honorifics:** Retain and italicize standard Islamic honorifics where
appropriate (e.g., *ṣallā Allāhu ʿalayhi wa-sallam*,
*nawwara Allāhu marqadahu*, *raḥmatullāhi ʿalayh*).

**Faithfulness:** Do not introduce new claims, stories, hadith, Qur'anic
references, names, or theological points that are not present in the source
translation. If the source contains uncertainty markers such as `[unclear]`,
do not silently delete them — either preserve the marker or phrase the
surrounding sentence cautiously so the gap remains visible.

## Output format

Return JSON with exactly the following keys (no extra prose outside the JSON
object):

- `title`: string, the article's title.
- `subtitle`: string or null. Use `null` if no subtitle is appropriate.
- `body_markdown`: the full article body in Markdown, using `##` and `###`
  headings and clear paragraph breaks. Italicized transliterations should
  be wrapped in `*…*`.

## Source (English translation)

{{SOURCE_TEXT}}
