University: [ITMO University](https://itmo.ru/ru/)  
Faculty: [FTMI](https://ftmi.itmo.ru)  
Course:  [Vibe Coding: AI-боты для бизнеса](https://github.com/itmo-ict-faculty/vibe-coding-for-business)  
Year: 2025/2026   
Group: U4225  
Author: Duchenko Yulia Viktorovna  
Lab: Lab3  
Date of create: 02.11.2025  
Date of finished: ___.11.2025 

## Отчет по практике - Лабораторная работа №3 "Запуск бота для реального использования"
-----------------
Во время выполнения задания выполнены следующие шаги:
1. В качестве способа деплоя выбрала Docker + VPS.
2. Добавила уточнения в файлы бота: .gitignore, requirements.txt, + "import logging".
3. Создала файлы: Dockerfile и docker-compose.yml
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/lab3_screen4.png)

4. Запустила бот через Docker
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/lab3_screen2.png)

5. Проверила работу и оставила на несколько часов.
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/lab3_screen3.png)

6. По итогу бот работает и не перегружает память
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/lab3_screen1.png)
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/lab3_screen5.png)

| Проверка | Результат | Комментарий |
| ------- | ------- | ------- |
| Запуск и инициализация | ✅ Успешно | Контейнер запущен, бот отвечает на команды |
| Ошибки в логах | ✅ Не обнаружено | За период наблюдения ошибок не зафиксировано |
| Использование CPU и памяти | ✅ В норме | CPU ≤ 2%, память стабильна около 120 МБ |
| Перезапуски контейнера | ✅ 0 | Контейнер работает непрерывно |
| Длительная работа (4 ч. => 24/48 ч.) | ✅ Устойчиво | Бот продолжает корректно отвечать, без пауз |
| Перезапуск вручную | ✅ Успешно | После docker restart бот активен и доступен |
| Healthcheck | ⚠️ Отключён | Проверка исключена из-за отсутствия procps в slim-образе, не влияет на стабильность |


7. Собрала обратную связь через форму https://forms.gle/2VqANJG4wMQ4Nwk26
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_survey1.png)   
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_survey2.png)   
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_survey3.png)   
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_survey4.png)   

По обратной связи: пользователю непонятно как вводить команды типа "/route <id|название> — маршрут в Яндекс.Картах", 
" /plan <id|название> — погода + маршрут". Также возможно дальнейшее улучшение бота через голосовой ввод и дополнительный контент.
Исходя из моих текущих навыков внесла следующие правки:
- упростила первую команду /find (чтобы не запрашивала id),
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_updates1.png)
- добавила команду для сбора обратной связи сразу в боте,
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_updates2.png)
- добавила команду для показа статистики админу.
![](https://github.com/juliadv8/2025-chatbots-U4225-duchenko_j_v/blob/main/lab3/img/l3_updates3.png)

------
[Ссылка на бот](https://t.me/itmoftmi_dyv_bot)  
[Cсылка на видео](https://drive.google.com/file/d/1yCI3LNYeJ40HJAa-AA8Q2aQQfRCerZOS/view?usp=sharing)
