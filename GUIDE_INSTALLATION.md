# Guide d'installation — Bot de trading crypto (OKX démo)

Ce guide suppose que tu n'as jamais utilisé GitHub ni tapé de commande.
Suis les étapes dans l'ordre, sans en sauter.

## Étape 1 — Créer tes clés API démo sur OKX

1. Connecte-toi sur www.okx.com
2. Va dans ton profil → cherche "Trading démo" ou "Demo Trading"
3. Une fois dans l'interface démo, va dans les paramètres API (souvent
   sous "API" ou "Gestion des clés API")
4. Crée une nouvelle clé API **en mode démo**
5. Note précieusement 3 informations (elles ne seront affichées qu'une
   seule fois) :
   - la clé API (API Key)
   - la clé secrète (Secret Key)
   - la phrase secrète (Passphrase) que tu choisis toi-même à la création
6. Pour les permissions de la clé, coche uniquement "Lecture" et
   "Trading" — jamais "Retrait"

## Étape 2 — Créer un compte GitHub (si tu n'en as pas)

1. Va sur github.com → "Sign up" → suis les instructions (gratuit)

## Étape 3 — Créer un nouveau dépôt (repository)

1. Une fois connecté, clique sur le bouton "+" en haut à droite → "New repository"
2. Donne-lui un nom, par exemple `bot-trading-crypto`
3. Choisis "Private" (pour que ton code ne soit pas visible publiquement)
4. Clique sur "Create repository"

## Étape 4 — Mettre les fichiers du bot dans le dépôt

1. Sur la page de ton nouveau dépôt, clique sur "uploading an existing file"
2. Glisse-dépose TOUS les fichiers de ce projet (garde bien la structure
   des dossiers, notamment le dossier `.github/workflows/`)
3. Clique sur "Commit changes" en bas de la page

## Étape 5 — Ajouter tes clés API en sécurité (secrets GitHub)

C'est l'étape la plus importante — tes clés ne doivent JAMAIS être
écrites directement dans le code, seulement stockées ici :

1. Dans ton dépôt GitHub, va dans "Settings" (paramètres du dépôt, pas
   ton profil)
2. Dans le menu de gauche : "Secrets and variables" → "Actions"
3. Clique sur "New repository secret" et ajoute, un par un :
   - Nom : `OKX_API_KEY` → valeur : ta clé API démo
   - Nom : `OKX_API_SECRET` → valeur : ta clé secrète
   - Nom : `OKX_PASSPHRASE` → valeur : ta passphrase
4. Clique sur "Add secret" à chaque fois

## Étape 6 — Activer les GitHub Actions

1. Va dans l'onglet "Actions" de ton dépôt
2. Si un message demande d'activer les workflows, clique pour confirmer
3. Tu devrais voir apparaître "Bot de trading horaire" dans la liste

## Étape 7 — Tester manuellement avant d'attendre l'heure pile

1. Dans l'onglet "Actions", clique sur "Bot de trading horaire"
2. Clique sur "Run workflow" → "Run workflow" (bouton vert) pour le
   lancer immédiatement, sans attendre la prochaine heure pile
3. Observe le résultat : si tout est vert, ça fonctionne. Si c'est
   rouge, clique dessus pour voir le message d'erreur (copie-le moi si
   tu ne comprends pas, je t'aiderai à le résoudre)

## Étape 8 — Suivre les résultats

- Le fichier `trading_log.csv` s'enrichit à chaque exécution avec
  chaque achat/vente
- Le fichier `state.json` contient l'état actuel (soldes de chaque
  stratégie, positions ouvertes)
- Tu peux ouvrir ces fichiers directement sur GitHub pour voir
  l'évolution, ou les télécharger et les ouvrir dans Excel

## Ce qu'il ne faut jamais faire

- Ne jamais changer `flag="1"` en `flag="0"` dans `okx_client.py` —
  ça basculerait en trading RÉEL avec de vrais fonds
- Ne jamais mettre tes clés API directement dans le code — toujours
  passer par les "Secrets" GitHub
- Ne jamais rendre le dépôt "Public" tant que tes secrets pourraient
  être visibles ailleurs dans le code
