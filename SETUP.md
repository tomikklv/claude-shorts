# SETUP: od nuly po prvý short

Predpoklady: Windows 10/11, platený plán Claude (kvôli Claude Code), ~16 GB RAM, ideálne NVIDIA GPU.
Pozor na práva: stream patrí streamerovi a hra vydavateľovi. Pred zverejňovaním klipov si vypýtaj súhlas streamera a vždy ho uveď.

## 1. Windows: WSL + Ubuntu
1. PowerShell ako správca: `wsl --install`, reštart, dokonči nastavenie Ubuntu (meno, heslo).
2. Voliteľne (pri 16 GB RAM pomáha proti pádom pri renderi) vytvor `C:\Users\<ty>\.wslconfig`:
   ```
   [wsl2]
   memory=11GB
   swap=8GB
   ```
   potom v PowerShelli `wsl --shutdown` a Ubuntu otvor znova.
3. Všetky ďalšie príkazy píš v Ubuntu termináli. Príkazy so `sudo` musíš písať ty sám (pýtajú heslo).

## 2. Základné nástroje (Ubuntu)
```
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip ffmpeg nodejs npm
node --version
```
Ak je Node starší ako 18, nainštaluj novší (napr. cez nvm).

## 3. Claude Code
```
sudo npm install -g @anthropic-ai/claude-code
claude
```
Prihlás sa účtom s platným plánom Claude. Na bežnú prácu stačí model Sonnet.

## 4. Skill z tohto repozitára
```
git clone https://github.com/tomikklv/claude-shorts ~/claude-shorts
mkdir -p ~/.claude/skills/shorts
cp -r ~/claude-shorts/{scripts,references,remotion,SKILL.md,requirements.txt} ~/.claude/skills/shorts/
```
(Súbor `SKILL.md` spomína `setup.sh`, `install.sh` a `presets/`. Tie v tejto kópii nie sú, kopíruješ ručne ako vyššie.)

Potom v priečinku skillu:
```
cd ~/.claude/skills/shorts
python3 -m venv ~/.shorts-skill
~/.shorts-skill/bin/pip install -r requirements.txt
cd remotion && npm install
```
Najjednoduchšie je nechať to na Claude Code: spusti `claude` a napíš „Spusti scripts/preflight.sh v ~/.claude/skills/shorts a doinštaluj všetko, čo chýba."

## 5. Streamer (preset)
```
mkdir -p ~/.config/claude-shorts
cp ~/claude-shorts/streamers.example.json ~/.config/claude-shorts/streamers.json
```
V súbore pridaj streamera (kľúč = meno, ktoré budeš písať do príkazu):
- `facecam` = poloha kamery ako zlomky obrazu (0–1): `x,y,w,h`. Ak ju nevieš, daj `null` (automatické hľadanie) alebo nechaj Claude Code zistiť ju zo screenshotu.
- `twitch` / `youtube` = skutočné odkazy na kanály streamera (over ich).

## 6. Prvý short
V `claude`:
```
/shorts gta <link na VOD streamu> <meno streamera>
```
Ukáže tabuľku kandidátov so skóre, vyrobí vybrané momenty a uloží ich. Najprv si pozri jeden hotový short a až potom rob viac.

## 7. Voliteľné: nahrávanie na YouTube
1. console.cloud.google.com → nový projekt → zapni „YouTube Data API v3".
2. Obrazovka súhlasu OAuth (Externá, Testovanie), pridaj sa ako testovací používateľ.
3. Vytvor OAuth klienta typu „Desktop app", stiahni JSON a ulož ho ako `~/.config/shorts/client_secret.json` (`chmod 600`). NIKDY ho nedávaj do repozitára ani do chatu.
4. Videá idú ako **súkromné**. Neoverené API projekty YouTube zamyká na súkromné, zverejnenie robíš ručne v YouTube Studiu. V testovacom režime token vyprší zhruba po 7 dňoch, treba sa prihlásiť znova.

## Časté problémy
- Pád WSL pri renderi: pridaj RAM/swap (`.wslconfig`) a renderuj po jednom klipe.
- Titulky pomalé: použi rýchly FFmpeg režim (`burn_captions.py`), Remotion je voliteľný.
- Chat z Twitchu chýba: doinštaluj TwitchDownloaderCLI (GitHub lay295/TwitchDownloader, Linux).
- Facecam zle vyrezaná: zadaj súradnice ručne.
- Čo je „claude doctor" varovanie o npm prefixe: neškodné, aktualizuj cez `sudo npm install -g @anthropic-ai/claude-code@latest`.
