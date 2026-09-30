import os
import openpyxl
from openpyxl.styles import PatternFill
from openpyxl.styles.colors import Color
from openpyxl.worksheet.table import Table
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.styles import Font
from openpyxl.worksheet.hyperlink import Hyperlink

def formate_excel():
    
    excel_FileName = get_latest_excel ()
    wb = openpyxl.load_workbook(excel_FileName)
    sheet_names = wb.sheetnames

    for i, sheet_name in enumerate(sheet_names):
        ws = wb[sheet_name]
        last_row = ws.max_row
        last_col = get_column_letter (ws.max_column)
        tab = Table(displayName=f'Table1{i}', ref="A1:"+ last_col + str(last_row))

        # Add a default style with striped rows and banded columns
        if sheet_name == "Summary" :
            TableStyleInfoName ="TableStyleMedium13"
        else: TableStyleInfoName ="TableStyleMedium2"

        style = TableStyleInfo(name=TableStyleInfoName, showFirstColumn=False,
                            showLastColumn=False, showRowStripes=True, showColumnStripes=False)
        tab.tableStyleInfo = style
        try:
            ws.add_table(tab)
        except:
            pass
        
        for col in ws.columns:
            max_length = 0
            columnValue = col[0].value  # Get the column name
            column = col[0].column_letter  # Get the column name
            if 'vendorname' in columnValue.lower():
                adjusted_width = 25
                ws.column_dimensions[column].width = adjusted_width
            else:
                adjusted_width = 20
                ws.column_dimensions[column].width = adjusted_width
            
            if 'invoiceiotal' in columnValue.lower():
                for cell in col:
                    if cell.row > 1:
                        cell.font=Font(color='000000FF',bold=True)
            if 'items_ref' in columnValue.lower():
                for cell in col:
                    if cell.row > 1 and cell.value is not None and int(cell.value) > 0:
                        cell.hyperlink = f"#'items_Ref_{cell.value}'!A1"
                        cell.style="Hyperlink"
        
        ws.sheet_view.showGridLines = False
 
    wb.save(excel_FileName)


def get_latest_excel ():
    current_directory = os.path.abspath(os.getcwd())
    path = os.path.join(current_directory, "Output")
    files = [f for f in os.listdir(path) if f.lower().endswith(('.xlsx', '.xlsm', '.xltx', '.xltm'))]
    if not files:
        raise FileNotFoundError(f"No Excel files found in {path}")

    latest = os.path.join(path, files[0])
    for key in files[1:]:
        candidate = os.path.join(path, key)
        if os.path.getctime(candidate) > os.path.getctime(latest):
            latest = candidate
    return latest

def main():
     formate_excel()
     
if __name__ == "__main__":
    main()



