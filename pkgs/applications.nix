{
  word = {
    command = "word365";
    name = "Word";
    executable = "WINWORD.EXE";
    wmClass = "winword.exe";
    category = "WordProcessor";
    color = "#185ABD";
    letter = "W";
    mimeTypes = [
      "application/msword"
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      "application/vnd.openxmlformats-officedocument.wordprocessingml.template"
      "application/vnd.ms-word.document.macroEnabled.12"
      "application/vnd.ms-word.template.macroEnabled.12"
    ];
  };
  excel = {
    command = "excel365";
    name = "Excel";
    executable = "EXCEL.EXE";
    wmClass = "excel.exe";
    category = "Spreadsheet";
    color = "#107C41";
    letter = "X";
    mimeTypes = [
      "application/vnd.ms-excel"
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      "application/vnd.openxmlformats-officedocument.spreadsheetml.template"
      "application/vnd.ms-excel.sheet.macroEnabled.12"
      "application/vnd.ms-excel.template.macroEnabled.12"
      "application/vnd.ms-excel.sheet.binary.macroEnabled.12"
      "application/vnd.ms-excel.addin.macroEnabled.12"
    ];
  };
  powerpoint = {
    command = "powerpoint365";
    name = "PowerPoint";
    executable = "POWERPNT.EXE";
    wmClass = "powerpnt.exe";
    category = "Presentation";
    color = "#C43E1C";
    letter = "P";
    mimeTypes = [
      "application/vnd.ms-powerpoint"
      "application/vnd.openxmlformats-officedocument.presentationml.presentation"
      "application/vnd.openxmlformats-officedocument.presentationml.template"
      "application/vnd.openxmlformats-officedocument.presentationml.slideshow"
      "application/vnd.ms-powerpoint.presentation.macroEnabled.12"
      "application/vnd.ms-powerpoint.template.macroEnabled.12"
      "application/vnd.ms-powerpoint.slideshow.macroEnabled.12"
    ];
  };
  outlook = {
    command = "outlook365";
    name = "Outlook";
    executable = "OUTLOOK.EXE";
    wmClass = "outlook.exe";
    category = "Email";
    color = "#0078D4";
    letter = "O";
    mimeTypes = [ "application/vnd.ms-outlook" ];
  };
  access = {
    command = "access365";
    name = "Access";
    executable = "MSACCESS.EXE";
    wmClass = "msaccess.exe";
    category = "Database";
    color = "#A4373A";
    letter = "A";
    mimeTypes = [
      "application/vnd.ms-access"
      "application/msaccess"
      "application/x-msaccess"
      "application/x-ms-windows-database"
    ];
  };
  publisher = {
    command = "publisher365";
    name = "Publisher";
    executable = "MSPUB.EXE";
    wmClass = "mspub.exe";
    category = "Publishing";
    color = "#077568";
    letter = "P";
    mimeTypes = [
      "application/vnd.ms-publisher"
      "application/x-mspublisher"
    ];
  };
  onenote = {
    command = "onenote365";
    name = "OneNote";
    executable = "ONENOTE.EXE";
    wmClass = "onenote.exe";
    category = "Office";
    color = "#7719AA";
    letter = "N";
    mimeTypes = [ "application/onenote" ];
  };
}
