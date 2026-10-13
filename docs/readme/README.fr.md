<p align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.ko.md">한국어</a> ·
  <a href="README.es.md">Español</a> ·
  <a href="README.pt-BR.md">Português (Brasil)</a> ·
  <strong>Français</strong>
</p>

<h1 align="center">Yugo - Blender Vtubing</h1>

<p align="center">
  <strong>Transformez Blender en un espace où combiner des données pour le VTubing et les expériences interactives.</strong>
</p>

<p align="center">
  <a href="https://discord.gg/w8QMZjVPtt"><img src="https://img.shields.io/badge/Discord-Join-5865F2?style=flat&amp;logo=discord&amp;logoColor=white" alt="Communauté Discord" height="24"></a>
  <a href="https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases"><img src="https://img.shields.io/badge/Release-Download-2ea44f?style=flat&amp;logo=github&amp;logoColor=white" alt="Télécharger Yugo" height="24"></a>
  <a href="https://hiiiodigital.com/Yugo-Docs/"><img src="https://img.shields.io/badge/Yugo%20DOC-Documentation-2980b9?style=flat&amp;logo=readthedocs&amp;logoColor=white" alt="Documentation Yugo DOC" height="24"></a>
</p>

<p align="center">
  <a href="https://hiiiodigital.com/Yugo-Docs/tutorials/getting-started.html">Démarrage rapide</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">Démonstrations</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">FAQ</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">Documentation</a>
</p>

Yugo - Blender Vtubing est un add-on (extension) pour Blender combinant le suivi en temps réel et des entrées interactives, comme les événements du clavier ou les messages du chat reçus en stream, le tout dans un seul et même flux de travail visuel. Rassemblez et combinez des données provenant de sources différentes afin de rendre votre personnage de VTubing plus vivant, puis utilisez ces mêmes blocs de construction pour explorer de nouvelles façons de faire interagir les personnages et les objets. La prise en charge du clavier et des messages du chat en stream est actuellement en cours de développement.

![Présentation d’un personnage rendu dans Blender](../images/yugo-character-preview.webp)

## Que peut-on y faire ?

- **Intégrez les mouvements et expressions en direct.** Recevez les mouvements du corps et les expressions via VMC, ainsi que le suivi facial via iFacialMocap. *D’autres méthodes de suivi sont en cours de développement.*
- **Définissez les mouvements et expressions.** Mélangez les sources, choisissez les parties du corps ou du visage qu’elles affectent, et ajustez la force, le lissage, l’effet miroir ainsi que le délai de chaque mouvement.
- **Ajoutez des interactions physiques.** Utilisez les réglages de corps rigides de Blender pour définir le comportement des objets de votre scène, puis combinez-les avec la physique des ressorts VRM (« spring physics ») pour les interactions entre personnages et objets.
- **Faites vos propres combinaisons.** Combinez plusieurs sources de mouvements avec des valeurs, des calculs, des paramètres temporels et des conditions pour créer vos propres configurations de performance et d’interaction en direct.

## Un espace pour les interactions créatives

L’idée est simple : importer des données dans Blender, les mélanger et les transformer, puis décider comment ces dernières agiront sur votre scène.

Yugo est conçu pour évoluer au-delà du simple contrôle d’avatar et devenir une boîte à outils pour les performances en direct, les interactions ludiques et les expérimentations directement dans Blender.

![Exemple de scène Yugo dans Blender](../images/yugo-blender-scene.webp)

*Une scène présentée lors de notre présentation à la BCON26.*

## Installation

**Yugo 0.2.3** inclut **Yugo Node** et les outils originaux de **Yugo** dans une seule extension pour Blender, sur **Windows x64**, **Linux x64** et **macOS sur Apple Silicon**, avec **Blender 5.1 ou 5.2**.

Téléchargez **yugo-0.2.3.zip** depuis la [page de publication de Yugo 0.2.3](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.3).
Dans Blender, ouvrez **Edit → Preferences → Get Extensions → Install from Disk**, sélectionnez le fichier ZIP et activez **Yugo**.

Dans la Vue 3D, appuyez sur **N**, accédez à l’onglet **Yugo** et utilisez **Open Yugo Node Editor** en haut de l’onglet. **Help** ouvre le manuel. Yugo Node est inclus par défaut ; aucune installation séparée n’est requise.

Si vous mettez à jour un ancien **Yugo 0.1.x**, sauvegardez votre scène, désactivez l’ancienne extension Yugo et installez le nouveau ZIP dans le même dépôt d’extensions. Redémarrez Blender et vérifiez que l’extension **Yugo** activée affiche bien la version **0.2.3**.
Si vous avez aussi installé un **Yugo Node** ou **Yugu Node Studio** séparément, désactivez cet ancien add-on avant d’activer Yugo. Les nouveaux utilisateurs peuvent ignorer ces étapes de mise à jour.

L’add-on optionnel [ProtoMotions / PhysX Tracker 0.4.0](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/download/v0.2.2/yugo_physx_tracker-0.4.0.zip) est un add-on séparé pour **Blender 5.1/5.2 sur Windows x64**. Son package existant et sa somme de contrôle (« checksum ») restent inchangés ; le support de PhysX pour Linux et macOS n’est pas inclus dans cette version.
Installez son ZIP (environ 242 Mo) après avoir activé Yugo, puis ouvrez l’éditeur de nœuds et choisissez **Add → PhysX Physics → Create PhysX Tracker Demo**.
Cliquez sur **Play** dans le nœud **PhysX Physics** pour tester le squelette fourni et l’exemple de marche. Votre propre VRM n’est pas requis pour cette démo.

- [Démarrage rapide sur Yugo DOC](https://hiiiodigital.com/Yugo-Docs/tutorials/getting-started.html)
- [Documentation sur Yugo DOC](https://hiiiodigital.com/Yugo-Docs/)
- [Rejoignez notre communauté Discord](https://discord.gg/w8QMZjVPtt)
- [Signaler un problème](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)

## License and acknowledgments

Yugo's original source code is open source under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. You may use, modify, and redistribute it, including commercially, under the GPL terms. Distributed modified versions must retain the GPL license and make the corresponding source code available to recipients. See [LICENSE](../../LICENSE) for the full terms.

Yugo builds on these open-source projects:

| Project | Use in Yugo | License |
| --- | --- | --- |
| [VRM Add-on for Blender](https://github.com/saturday06/VRM-Addon-for-Blender/blob/main/LICENSE_MAIN.txt) | VRM character data and integration | MIT OR GPL-3.0-or-later |
| [MuJoCo](https://github.com/google-deepmind/mujoco/blob/3.3.7/LICENSE) | Physics simulation | Apache-2.0 |

## Remerciements

Merci à toutes les personnes qui contribuent au code, aux traductions, au design, à la documentation et aux tests de Yugo.

| Contributeur | Contribution |
| --- | --- |
| **Jems / L’Archiviste** | Traduction française du README |
