export type Command = { name: string; desc: string; args?: string };

export const COMMANDS: Command[] = [
  { name: "help", desc: "Показать список команд" },
  { name: "save", desc: "Сохранить воспоминание", args: "<текст>" },
  { name: "search", desc: "Семантический поиск по памяти", args: "<запрос>" },
  { name: "recent", desc: "Последние воспоминания", args: "[n]" },
  { name: "tags", desc: "Список тегов" },
  { name: "tag", desc: "Добавить тег к записи", args: "<id> <тег>" },
  { name: "forget", desc: "Удалить воспоминание", args: "<id>" },
  { name: "summary", desc: "Резюме памяти через LLM", args: "[n]" },
  { name: "context", desc: "Что подставляется в промпт", args: "[запрос]" },
  { name: "export", desc: "Экспорт всей памяти в JSON" },
  { name: "clear", desc: "Очистить текущий чат" },
  { name: "think", desc: "Режим размышлений", args: "on|off" },
  { name: "wipe", desc: "Удалить ВСЮ память", args: "everything" },
  { name: "status", desc: "Статус приложения" },
];
