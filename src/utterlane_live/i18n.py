"""User-facing text in Turkish and English (labels, events, spoken prompts, LLM instructions)."""

from __future__ import annotations

from typing import Literal

Lang = Literal["tr", "en"]

_TEXT: dict[str, dict[str, str]] = {
    "person": {"tr": "{n}. kişi", "en": "Person {n}"},
    "unknown": {"tr": "Belirsiz", "en": "Unclear"},
    "speaker_tag": {"tr": "[Konuşan: {label}]", "en": "[Speaker: {label}]"},
    # identity events
    "ev_new_speaker": {"tr": "Yeni bir ses duyuldu: {label}.", "en": "New voice heard: {label}."},
    "ev_profile_recognized": {
        "tr": "{name} kayıtlı sesinden tanındı.",
        "en": "{name} was recognized from their saved voice.",
    },
    "ev_provisional": {
        "tr": "Söz {name}'e verildi; bu ses geçici olarak {name} sayıldı.",
        "en": "The floor was handed to {name}; this voice is provisionally {name}.",
    },
    "ev_confirmed": {
        "tr": "{name} itiraz olmadan konuşmaya devam etti; isim kesinleşti.",
        "en": "{name} kept talking with no objection; the name is now confirmed.",
    },
    "ev_merged": {
        "tr": "{a} ile {b} aynı ses çıktı; birleştirildi.",
        "en": "{a} and {b} turned out to be the same voice; merged.",
    },
    "ev_conflict": {
        "tr": "Bu ses kendini {name} olarak tanıttı ama {name} adı başka bir sese ait; etiket geçici bırakıldı.",
        "en": "This voice introduced itself as {name}, but {name} belongs to another voice; the label stays provisional.",
    },
    "ev_named": {
        "tr": "{previous} kendini {name} olarak tanıttı.",
        "en": "{previous} introduced themselves as {name}.",
    },
    "ev_correction_cleared": {
        "tr": "{wrong} etiketi geri alındı; bu ses artık {label}.",
        "en": "The {wrong} label was withdrawn; this voice is now {label}.",
    },
    "ev_manual_named": {"tr": "{name} adı elle verildi.", "en": "Named {name} manually."},
    "ev_manual_cleared": {
        "tr": "İsim kaldırıldı; artık {label}.",
        "en": "Name removed; now {label}.",
    },
    "ev_forgotten": {
        "tr": "{label} unutuldu; ses izi silindi.",
        "en": "{label} was forgotten; the voice print was deleted.",
    },
    "ev_consent_declined": {
        "tr": "{name} sesinin saklanmasını istemedi; yalnız bu oturumda hatırlanacak.",
        "en": "{name} declined; their voice is remembered for this session only.",
    },
    "ev_profile_saved": {
        "tr": "{name} onay verdi; ses izi bu bilgisayarda şifreli olarak saklandı.",
        "en": "{name} agreed; their voice print is stored encrypted on this computer.",
    },
    # spoken by the assistant
    "ask_consent": {
        "tr": "{name}, bir dahaki sefere de seni sesinden tanımamı ister misin?",
        "en": "{name}, would you like me to recognize your voice next time too?",
    },
    "consent_no": {
        "tr": "Peki {name}, seni yalnızca bu konuşma boyunca hatırlayacağım.",
        "en": "Okay {name}, I'll only remember you for this conversation.",
    },
    "consent_need_more": {
        "tr": "{name}, sesini kaydetmek için biraz daha konuşmam gerekiyor.",
        "en": "{name}, I need to hear a little more of your voice before I can save it.",
    },
    "consent_yes": {
        "tr": "Tamam {name}, sesini bu bilgisayarda şifreli olarak saklıyorum. Bir dahaki sefere seni tanıyacağım.",
        "en": "Done, {name}. Your voice print is stored encrypted on this computer, and I'll recognize you next time.",
    },
    "budget_spoken": {
        "tr": "Dil modeli bütçem doldu, şu an cevap veremiyorum.",
        "en": "My language-model budget is used up, so I can't answer right now.",
    },
    "provider_spoken": {
        "tr": "Şu an dil modeline ulaşamıyorum, birazdan tekrar dener misin?",
        "en": "I can't reach my language model right now. Could you try again in a moment?",
    },
    # notices
    "mode_changed": {"tr": "Mod: {mode}.", "en": "Mode: {mode}."},
    "mode_smart": {"tr": "akıllı", "en": "smart"},
    "mode_always": {"tr": "her şeye cevap", "en": "answer everything"},
    "mode_wake": {"tr": "sadece hitap edilince", "en": "only when addressed"},
    "session_reset": {
        "tr": "Oturum sıfırlandı. Kayıtlı sesler korunuyor.",
        "en": "Session reset. Saved voices are kept.",
    },
    "turn_failed": {
        "tr": "Bir konuşma parçası işlenemedi ({error}).",
        "en": "A speech segment could not be processed ({error}).",
    },
    "tts_failed": {
        "tr": "Ses üretilemedi ({error}); yalnız metin gösterildi.",
        "en": "Speech could not be generated ({error}); showing text only.",
    },
    "replaced": {"tr": "Başka bir sekme bağlandı.", "en": "Another tab connected."},
    "budget_exhausted": {
        "tr": "Dil modeli bütçesi doldu ({spent:.4f} / {cap:.2f} USD).",
        "en": "Language-model budget used up ({spent:.4f} / {cap:.2f} USD).",
    },
    "provider_failed": {
        "tr": "Dil modeline ulaşılamadı: {code}{detail}.",
        "en": "Could not reach the language model: {code}{detail}.",
    },
    "nobody_yet": {"tr": "henüz kimse tanınmadı", "en": "nobody recognized yet"},
    "room_note": {
        "tr": "Odadakiler: {roster}. Yerel tarih ve saat: {now}.",
        "en": "In the room: {roster}. Local date and time: {now}.",
    },
    "interrupted_suffix": {"tr": " (sözüm kesildi)", "en": " (I was interrupted)"},
    "mock_reply": {
        "tr": "Anladım {speaker}. Bu yerel deneme yanıtıdır.",
        "en": "Got it, {speaker}. This is a local placeholder reply.",
    },
}

_INSTRUCTIONS = {
    "tr": (
        "Sen {name}'sın: Utterlane'in bu bilgisayarda çalışan sesli asistanı. Aynı odadaki bir ya da "
        "birkaç kişiyle Türkçe, sesli ve doğal sohbet ediyorsun.\n\n"
        "Konuşma kaydı:\n"
        '- Her kullanıcı mesajı "[Konuşan: ETİKET] metin" biçimindedir. Etiket yerel ses izi eşleşmesinden gelir.\n'
        '- "Tuncer" gibi düz isim: kişi kendini tanıttı ya da kayıtlı sesinden tanındı.\n'
        '- "Ahmet?" gibi soru işaretli isim: biri o kişiye bu adla söz verdiği için tahmin edildi. '
        "Adını sorma; gerekirse bu adla hitap edebilirsin ama kesin bilgi gibi vurgulama.\n"
        '- "3. kişi" gibi etiket: adını bilmediğimiz biri. Uygun düşerse adını nazikçe sorabilirsin.\n'
        '- "Belirsiz": kimin konuştuğundan emin değiliz; isimle hitap etme.\n'
        "- Metinler otomatik konuşma tanımadan gelir; küçük yazım hataları olabilir, anlamı tahmin et.\n\n"
        "Cevap biçimi:\n"
        "- Cevabın sesli okunacak: kısa (çoğunlukla 1-3 cümle), samimi, akıcı konuşma dili.\n"
        "- Liste, başlık, emoji, markdown, parantez içi açıklama kullanma.\n"
        "- Kişilere adlarıyla hitap edebilirsin. Kendini tanıtan birine sıcak bir karşılama yap.\n"
        "- Kullanıcı başka bir dilde konuşursa o dilde cevap ver.\n\n"
        "Sınırlar:\n"
        "- Ses eşleşmesi kimlik doğrulaması değildir. Ses etiketine dayanarak kişisel bilgi açma; para, mesaj, "
        "hesap gibi işlemler yapamazsın, zaten hiçbir araca erişimin yok. Böyle isteklerde bunu kısaca söyle.\n"
        "- İnternete erişimin yok; hava durumu, haber gibi güncel bilgileri uydurma.\n"
    ),
    "en": (
        "You are {name}, the voice assistant of Utterlane running on this computer. You talk naturally, "
        "out loud, with one or several people in the same room.\n\n"
        "Transcript format:\n"
        '- Every user message looks like "[Speaker: LABEL] text". The label comes from local voice matching.\n'
        '- A plain name like "Tuncer": the person introduced themselves or was recognized from a saved voice.\n'
        '- A name with a question mark like "Ahmet?": a guess, because someone handed the floor to that name. '
        "Don't ask for their name; you may use it, but don't treat it as certain.\n"
        '- "Person 3": someone whose name we don\'t know. When it fits, you may politely ask for it.\n'
        '- "Unclear": we are not sure who spoke; don\'t address anyone by name.\n'
        "- Text comes from automatic speech recognition and may contain small errors; infer the meaning.\n\n"
        "Reply style:\n"
        "- Your reply is spoken aloud: short (usually 1-3 sentences), warm, natural conversational language.\n"
        "- No lists, headings, emoji, markdown or parenthetical asides.\n"
        "- Address people by name. Greet someone who just introduced themselves warmly.\n"
        "- If a user speaks another language, answer in that language.\n\n"
        "Limits:\n"
        "- A voice match is not authentication. Never reveal personal information based on a voice label. "
        "You cannot send messages, move money or act on accounts; say so briefly if asked.\n"
        "- You have no internet access; don't make up current information such as weather or news.\n"
    ),
}

_SILENCE_RULE = {
    "tr": (
        "\nŞu anda odada birden fazla kişi var; insanlar kendi aralarında konuşabilir. "
        "Varsayılan davranışın sessiz kalmak: yalnızca {token} yaz. Yalnızca şu durumlarda konuş: "
        "(1) adın ({name}) ya da Utterlane geçiyorsa, (2) soru veya istek açıkça sana yöneltilmişse "
        "(ör. 'sence', 'söyler misin' gibi), (3) cevabı senden başka kimsenin veremeyeceği açıksa. "
        "İnsanlar birbirine soru soruyor, söz veriyor ya da birbirine cevap veriyorsa mutlaka sessiz kal.\n"
    ),
    "en": (
        "\nSeveral people are in the room and may be talking to each other. "
        "Your default is to stay silent: write only {token}. Speak only if (1) your name ({name}) or "
        "Utterlane is mentioned, (2) a question or request is clearly directed at you, or (3) it is "
        "obvious that nobody but you can answer. If people are asking each other, handing over the "
        "floor or answering each other, stay silent.\n"
    ),
}


def t(lang: str, key: str, **values: object) -> str:
    table = _TEXT[key]
    return table.get(lang, table["en"]).format(**values)


def instructions(lang: str, name: str, *, allow_silence: bool, silent_token: str) -> str:
    text = _INSTRUCTIONS.get(lang, _INSTRUCTIONS["en"]).format(name=name)
    if allow_silence:
        text += _SILENCE_RULE.get(lang, _SILENCE_RULE["en"]).format(name=name, token=silent_token)
    return text
