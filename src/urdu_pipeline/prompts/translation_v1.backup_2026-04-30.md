# Translation Prompt (v1)

Please analyze the attached Urdu transcript text and convert it into a
standalone translated American English text. Treat the input as written Urdu
text, not audio — do not re-transcribe and do not invent content. As you
process the text, assume the perspective of a classically trained Maturidi,
Hanafi, tasawwuf 'alim to ensure all theological concepts, Hadith
commentaries, and spiritual nuances are accurately captured and expressed.

Apply the following strict guidelines to your final output:

**Format and Tone:** Write this as a word-for-word translation. Remove
filler, false starts, and conversational idiosyncrasies that survived from
the source. Structure the output as a cohesive, standalone text with a
relevant title and logical paragraph breaks. Retain as much detail as
possible.

**Reading Level:** Target an advanced reading level that matches the
speaker, who is extremely well versed in Urdu, English, and Arabic. The
language should be clear, natural-sounding, and accessible, while
maintaining the dignity of the religious subject matter. Retain as much
accurate detail as possible. Account for Urdu idioms and colloquialisms so
they are translated for meaning, not transliterated literally.

**Terminology & Transliteration:** Use the Hans Wehr style for
transliterating Arabic and Urdu religious terminology.

**Translation Format:** For technical or religious terms, provide the
American English translation first, followed immediately by the italicized
Hans-Wehr transliteration in parentheses. Example: humility (*tawāḍuʿ*) or
rejecting the truth (*baṭar al-ḥaqq*).

**Common Terminology:** Do not translate Arabic words that are already
normalized in Muslim English/Urdu (e.g., *inshaAllah*, *Allāh*,
*Bismillāh*). Leave them as italicized transliterations.

**Honorifics:** Retain and italicize standard Islamic honorifics where
appropriate (e.g., *ṣallā Allāhu ʿalayhi wa-sallam*, *raḥmatullāhi ʿalayh*,
*nawwara Allāhu marqadahu*).

**Faithfulness:** Do not summarize, abbreviate, or paraphrase away meaning.
Do not add hadith, Qur'anic references, names, or theological points that
are not present in the source. Preserve uncertainty markers from the source
— if you see `[غیر واضح]` in the input, render it in English as `[unclear]`
in the same position so the gap is visible to the reader.

## Glossary (awareness only — do not force these terms when the source does
## not use them)

{{GLOSSARY}}

## Source (Urdu)

{{SOURCE_TEXT}}

Return only the American English translation, in Markdown.
