# Codex project instructions

Follow explicit user instructions and existing repository security policies. Never merge, deploy, publish, delete data, weaken permissions, or change secrets without explicit authorization for that action.

Choose orchestration depth by task:
- **Quick:** answer directly or make one localized edit; no compulsory subagents.
- **Standard:** use the smallest useful set of explorer, worker, researcher, or tester for scoped multi-file work.
- **Full:** for high-risk or independent workstreams, plan ownership, delegate bounded tasks, validate changes, and request independent reviewer/visual-inspector findings as relevant.

Root owns design choices, integration and final verification. Assign one writer per file, limit concurrent agents to the configured cap, and never treat another agent's report as test evidence. Read-only agents must not modify files. Keep user-facing reports short, state which tests actually ran, and mark unsupported claims NOT CHECKED.

When tools or roles are unavailable, continue safely with available tools and disclose the limitation; do not pretend delegation or checks happened.

## Строительная геодезия и выпуск чертежей

Этот раздел применяется к проектам строительной геодезии с профилем Plus и установленными геодезическими навыками.

- Полевые данные, импорт XYZ, система координат и высот → geo-survey.
- Исполнительная схема или ведомость фактических измерений → geo-asbuilt.
- Сопоставление проекта и факта, отклонения → geo-deviation.
- Колодцы, трубопроводы, профили и уклоны → geo-networks.
- Применимость ГОСТ, СП или проектных требований → geo-standards.
- Проверка листов, выносок, печати и масштабов → geo-visual-qa.

Простую задачу выполняет основной агент. Для сложной работы подключай только нужные роли. Visual-inspector нужен для проверки реальных изображений листов. Для ответственного выпуска проведи техническую и визуальную проверку по исходным данным.

В задачах этой среды используй только обычный Autodesk AutoCAD 2018. До запуска и подключения подтверди продукт, путь, параметры запуска, профиль и экземпляр. Civil 3D, его профиль запуска «as AutoCAD», COM/Aecc и системные ассоциации DWG запрещены. При неоднозначности останови CAD-операцию. Перед записью проверь полный путь DWG и работай с копией без разрешения на изменение оригинала. При расхождении системы координат, высот или единиц приостанови расчёт затронутых результатов. Ссылайся на пункт источника любого применённого допуска.
