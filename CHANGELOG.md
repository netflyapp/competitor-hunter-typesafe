# CHANGELOG

Rozdziały są odwrócone względem gównego upstreamu: najnowszy na górze,
potem historia oryginału. Wszystko po separatorach `---` poniżej pochodzi
od Charlesa Dove (Charlie Automates), MIT.

---

## 2026-10-07 (3) — Adapter redukowany z 739 do 201 linii

Pierwsza wersja forku skopiowała całego `jev.py` ze skilla i poprawiła cztery
miejsca. Działało, ale tworzyło **739 linii do utrzymania**, z których apka
używa 7 funkcji — reszta to CLI, którego nigdy nie wywołuje.

Teraz `engine/jev_typesafe.py` jest **mostem, nie kopią**: uruchamia skill w
jego własnym module i podmienia wyłącznie transport. Logika decyzyjna —
`flatten_answers`, `apply_rules`, `is_sure`, `lint_preset`, `load_preset` —
pochodzi ze skilla, więc `npx claude-x-jev install` dociera tu ze swoimi
zmianami automatycznie.

### Podział

| Wiadomo | Skąd |
|---|---|
| parsowanie odpowiedzi, reguły, pewność, lint, presety | skill, bez zmian |
| `post_json`, `get_json` (transport) | skill, bez zmian |
| retry i obsługa błędów HTTP | skill, przez opakowanie `decide` |
| URL, nazwa modelu, wybór klucza, koszt, `status` | most |

Weryfikacja pochodzenia (nie tylko obecności):

```
flatten_answers  <- scripts/jev.py
apply_rules      <- scripts/jev.py
is_sure          <- scripts/jev.py
lint_preset      <- scripts/jev.py
load_preset      <- scripts/jev.py
decide           <- MOST
api_key          <- MOST
```

### Przywrócono obsługę obu dostawców

Pierwsza wersja forku wiedziała tylko o TypeSafe. Teraz wybór jest automatyczny
i nadpisywalny:

- klucz `apikey_…` → TypeSafe
- klucz `sk-or-…` → OpenRouter
- `JEV_PROVIDER=openrouter` wymusza jawnie

`api_key()` dopasowuje klucz do prefiksu dostawcy, więc klucz jednego
dostawcy nigdy nie trafi do endpointu drugiego — dokładnie ten błąd, który
zepsuł pierwszy hunt.

### Poprawiony błąd CLI

`skill.main()` wiąże subkomendę `status` z nazwy w **przestrzeni nazw skilla**,
więc nadpisane `cmd_status` w moście nigdy nie zostało wywołane — zamiast tego
leciał oryginalny i zgłaszał brak `OPENROUTER_API_KEY`. Naprawione przez
podmianę referencji przed `skill.main()`. To nie było widoczne w testach
apki, bo apka nie woła CLI.

### Weryfikacja

| Test | Wynik |
|---|---|
| `jev status` | provider, klucz, model, live call 322 ms |
| `jev lint --preset ...` | clean |
| `jev presets` | 4 presety ze skilla |
| 7 pytań przez most | 17 pól, `sure: true` |
| Pełny hunt, 3 kanały | 173 posty, $0.0114, 0 błędów |

---

## 2026-10-07 — Fork: bezpośrednie połączenie z konsolą TypeSafe

Zamiast przez OpenRouter. Model, cena i ranking bez zmian.

**Dlaczego.** Klucz OpenRouter bez kredytów zwracał `HTTP 402`, więc hunt
nie ruszał. Konsola TypeSafe daje klucz z miesięcznym kredytem.

**Kluczowa decyzja: nie modyfikować pliku skilla.** `claude-x-jev` w
`~/.claude/skills/` jest nadpisywany przez `npx claude-x-jev install`.
Zamiast tego caller leży w repo jako `engine/jev_typesafe.py` i jest
domyślną ścieżką (`hunt.py`, `JEV_PY`). Instalacja skilla przestaje być
wymagana, a `local.json` → `jev_py` pozwala wrócić do niej.

### Zmienione

| Plik | Zmiana |
|---|---|
| `engine/jev_typesafe.py` | **nowy.** Caller na `api.typesafe.ai/v1/systemone`, mapowanie modelu, lokalne liczenie kosztu |
| `engine/hunt.py` | `JEV_PY` domyślnie wskazuje na dołączony caller, nie na skill |
| `server.py` | `jev_keys()` czyta `TYPESAFE_API_KEY`/`JEV`/`OPENROUTER_API_KEY`; `child_env()` eksportuje klucz pod nazwą, którą czyta engine; `jev_credits()` nie odpytuje OpenRouter, gdy klucz jest z TypeSafe |
| `web/app.js` | komunikat błędu nie mówi już „OPENROUTER_API_KEY" |
| `.env.example` | `TYPESAFE_API_KEY` jako opcja główna |
| `local.example.json` | bezpieczne wartości domyślne zamiast placeholderów `/path/to/...` |
| `.gitignore` | dodane `.venv/`, `reports/`, `TASK.md`, `kanaly_yt_ig.md` |
| `README.md` | sekcja o tym, co ten fork zmienia |

### Cztery rzeczy wymagające obsługi przy zamianie

1. **Nazwa modelu.** Preset zawiera `typesafe/jev-1.13` (format OpenRouter).
   TypeSafe odpowiada `400 Unknown model`. Dodano mapowanie aliasów, dzięki
   czemu **presetów nie trzeba przerabiać**.
2. **Brak kosztu w odpowiedzi.** TypeSafe zwraca tylko `input_tokens`/
   `output_tokens`, a engine czyta `usage.cost`. Koszt liczony lokalnie po
   oficjalnej stawce, z flagą `cost_is_estimate`.
3. **Brak endpointu salda.** Sprawdzone `/v1/credits`, `/v1/balance`,
   `/v1/usage`, `/v1/account`, `/v1/me` — wszystkie 404. `jev_credits()`
   zwraca `null`, UI pokazuje „Jev gotowy" bez kwoty.
4. **`cmd_status`** odwoływał się do usuniętych endpointów.

### Błąd znaleziony przy pierwszym uruchomieniu przez API

Pierwszy hunt odpalony tą ścieżką zakończył się:

```
[pilot/error] Jev failed on 40 of 40 items: HTTP 401
```

Klucz leżał w `.env` jako `JEV`, a caller czytał `TYPESAFE_API_KEY` i
`OPENROUTER_API_KEY`. Do procesu potomnego trafił klucz OpenRouter, który
poszedł do endpointu TypeSafe. Testy ręczne przechodziły, bo zmienna była
ustawiana ręcznie w powłoce — błąd był widoczny **tylko** w prawdziwym
uruchomieniu. Naprawiony w obu miejscach, żeby nie zależał od nazwy zmiennej,
plus `api_key()` pomija klucze `sk-or-…`, dopóki nie ma lepszego.

### Weryfikacja

| Przebieg | Wynik |
|---|---|
| Pełny hunt, 15 kanałów YT (ręcznie) | 1013 postów, $0.0695, 0 błędów |
| Pełny hunt, 38 kanałów YT+IG (przez API) | 2125 postów, $0.1361, 2 błędy |
| Bez `local.json`, bez skilla | wszystkie health-checki zielone |
| `pilot` na bazowym presecie | health 0.806, 0 flagów |

Konduktor (Claude Code jako dyrygent) wsiada poprawnie, ale wymaga konta z
limitem — przy wyczerpanym limicie serwer przejmuje etapy sam i hunt kończy
się na bazowym presecie. Fallback przetestowany i działa.

### Koszty zmierzone

```
Jev, 29 kanałów YT, ~1500 postów   ~$0.14
Apify Instagram, 618 postów        ~$1.42     <- 10x drożej niż cały Jev
```

---

## Upstream — historia oryginału

Poniżej changelog Charlesa Dove. Wersja: `e6db1e2`. Pełna historia w
[git log](https://github.com/charlesdove977/competitor-hunter/commits/main).