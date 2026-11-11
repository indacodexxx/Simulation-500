# Simulation 500 — banc d'essai des stratégies

Le fichier `research.py` est un moteur de **recherche hors ligne**, distinct du bot fictif en production. Il ne place aucun ordre réel ou simulé sur le serveur.

## Utilisation

```bash
python research.py --csv candles.csv --asset-type crypto
python research.py --csv actions.csv --asset-type equity
```

Le CSV doit contenir au moins 240 bougies triées par date, sans doublon, avec les colonnes `timestamp,open,high,low,close` (volume facultatif). Les prix doivent être positifs et cohérents. Pour comparer des instruments en USD et EUR, utiliser des séries homogènes par devise.

## Méthode

- Séparation chronologique : 70 % d'entraînement et 30 % de test.
- Quatre stratégies long-only : momentum, retour à la moyenne, breakout, tendance EMA.
- Signaux calculés sur les bougies clôturées, entrée/sortie à l'ouverture de la bougie suivante (pas de fuite future intentionnelle).
- Frais estimés : crypto 0,4 % de commission par côté, spread complet 0,1 %, slippage 0,05 % par côté ; actions/ETF 0,1 % de commission, spread complet 0,05 %, slippage 0,05 %.
- Le choix de la stratégie est effectué **sur l'entraînement uniquement**. Le test hors échantillon est publié pour toutes les stratégies, sans re-sélection.
- Référence buy-and-hold sur la période de test.

## Limites

Le test est volontairement simple : pas de bid/ask historique réel, profondeur du carnet, latence, financement, dividendes, impôts, variations des frais ou interruption des données. Les chiffres ne démontrent pas une rentabilité future. Le rapport doit être complété par des tests walk-forward multiples et des contrôles d'exécution avant toute utilisation réelle.

Le portefeuille de trading fictif sur Render reste indépendant. La base SQLite de l'offre gratuite peut disparaître lors d'un redéploiement. Aucune série historique n'est fournie ni résultat inventé.
