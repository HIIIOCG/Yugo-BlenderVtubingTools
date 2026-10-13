<p align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.zh-TW.md">繁體中文</a> ·
  <a href="README.ja.md">日本語（Prochainement）</a> ·
  <a href="README.ko.md">한국어（Prochainement)</a><br>
  <a href="README.es.md">Español（Prochainement）</a> ·
  <a href="README.pt-BR.md">Português (Brésil)（Prochainement）</a> ·
  <strong>Français</strong>
</p>

<h1 align="center">Yugo - Blender Vtubing</h1>
<p align="center">
  <strong>Transformer Blender en votre nouvel environnement de Vtubing.</strong>
</p>
<p align="center">
  <a href="https://discord.gg/w8QMZjVPtt"><strong>Discord</strong></a> ·
  <a href="https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases"><strong>Release</strong></a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/"><strong>Yugo DOC</strong></a>
</p>
<p align="center">
  <a href="https://hiiiodigital.com/Yugo-Docs/tutorials/getting-started.html">Démarrage rapide</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/index.html#what-can-you-do">Que peut-on y faire ?</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">Showcase (Prochainement)</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">FAQ (Prochainement)</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">Documentation</a>
</p>
Yugo DOC est LE document regroupant tous les tutoriels, références de nœuds (Logique nodale) ainsi que les ressources venant d'autres projets. Les pages dédiées aux démonstrations et FAQ seront disponibles prochainement ; ces deux liens ouvrent la page principale du Yugo DOC pour le moment.

Yugo - Blender Vtubing est un add-on (extension) pour Blender combinant suivi et gestion temps réel d'événements comme les raccourcis clavier ou les messages du chat reçus en stream, tout ceci en un seul est même espace de travail. Rassemblez et combinez diverses informations/données provenant de sources différentes afin de rendre votre personnage de Vtubing plus vivant que jamais, puis utilisez ces mêmes blocs de construction pour explorer l'infinité d’interactions possibles, entre votre avatar, et votre propre monde 3D.

La prise en charge du clavier et des messages du chat en stream est actuellement en cours de développement.

![Character showcase rendered in Blender](../images/yugo-character-preview.webp)

## Que peut-on y faire ?

- **Performez à une toute nouvelle échelle** Retranscrivez les mouvements du corps et ses expressions grâce à VMC, et le suivi facial grâce à iFacialMocap. *D’autres méthodes de suivi sont en cours de développement.*
- **Définissez les mouvements et expressions** Mélanger les sources, choisissez quelle partie du corps ou du visage sont affectée, et ajustez la force, le lissage, la copie en miroir, ainsi que le délai de chaque mouvement.
- **Ajoutez des interactions physiques** Utilisez le système de corps rigide de Blender pour définir comment les objets de votre scène interagissent, puis combinez-les avec la technologie VRM "spring physics" pour les interactions de personnages et objets.
- **Faites vos propres combinaisons** Combinez plusieurs sources de mouvements avec des valeurs, des math, du timing et des conditions pour créer votre propre setup de performances et d’interaction direct live.

## L'environnement pour des interactions créatives.

L'idée est simple : importer des données dans Blender, les mélanger et les transformer, puis décider comment ces dernières interagiront avec votre scène.

Yugo est conçu pour s'épanouir au-delà du simple contrôle d'avatar. Yugo est une boîte à outil pour les performances en direct, les interaction enjouée, et pour expérimenter dans Blender directement.

![Example Yugo scene inside Blender](../images/yugo-blender-scene.webp)

*Scène incluse dans notre présentation à la BCON26.*

## Installation

**Yugo 0.2.3** inclut **Yugo Node** et les outils originaux de **Yugo** dans une seule extension pour Blender sous : Windows x64, Linux x64, et macOS Apple Silicon, pour les versions 5.1 et 5.2 de Blender.
Les Mac avec puces Intel et les versions 4.5 de Blender ont besoin quant à eux, d'une mise à jour de compatibilité séparée de Yugo.

Téléchargez **yugo-0.2.3.zip** dès maintenant : [Yugo 0.2.3 release](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.3).
Pour l'ajouter à Blender :
Glissez-déposez le fichier .ZIP dans la Vue 3D de votre Blender puis accepter la pop-up d'installation.

Dans la Vue 3D, appuyez sur N, accédez au menu Yugo, et utilisez **Open Yugo Node Editor** en haut du menu. **Help** ouvre le manuel. Yugo Node est inclus par défaut ; aucune installation séparée n'est requise.

Si vous mettez à jour un ancien **Yugo 0.1.x**, sauvegardez votre scène, désactivez l'ancienne extension Yugo, et installez le nouveau .ZIP dans le même répertoire. Redémarrez Blender et vérifiez que l'extension activée affiche bien la version installée.
Si vous avez aussi un Yugo Node ou Yugo Node Studio de façon séparée, désactivez ces anciens add-ons avant d'activer Yugo.
PS : Les nouveaux utilisateurs n'ont pas besoin de suivre ces étapes.

L'add-on optionnel [ProtoMotions / PhysX Tracker 0.4.0](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/download/v0.2.2/yugo_physx_tracker-0.4.0.zip) est un add-on séparé pour Blender 5.1/5.2 Windows x64. Son package et son checksum préexistants n'ont pas changé ; le support de PhysX pour Linux et macOS n'est pas inclus.
Installez son .ZIP (environ 242 MB) après avoir activé Yugo, puis ouvrez l'éditeur de noeuds et choisissez **Add → PhysX Physics → Create PhysX Tracker Demo**.
Cliquez sur **Play** dans le noeud **PhysX Physics** pour tester le squelette fourni et l'exemple de marche. Votre propre VRM n'est pas requis pour cette démo.

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
