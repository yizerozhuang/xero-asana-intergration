from xero_python.accounting import AccountingApi
import asana
import dateutil.parser
from config import CONFIGURATION as conf
from xero_function import xero_token_required, refresh_token, get_xero_tenant_id, api_client
from asana_function import clean_response, name_id_map

import os
import json
import time
import shutil
from datetime import date
from win32com import client as win32client


database_dir = conf["database_dir"]
invoice_dir = os.path.join(database_dir, "invoices.json")
invoice_json = json.load(open(invoice_dir))
bill_dir = os.path.join(database_dir, "bills.json")
bill_json = json.load(open(bill_dir))

# generate_management_report = False

if_modified_since = dateutil.parser.parse("2024-01-01")

asana_configuration = asana.Configuration()
asana_configuration.access_token = '2/1203283895754383/1206354773081941:c116d68430be7b2832bf5d7ea2a0a415'
asana_api_client = asana.ApiClient(asana_configuration)
custom_fields_api_instance = asana.CustomFieldsApi(asana_api_client)
custom_fields_setting_api_instance = asana.CustomFieldSettingsApi(asana_api_client)
invoice_status_asana_gid = "1203204784052206"
bill_status_asana_gid = "1203219273836510"
# project_api_instance = asana.ProjectsApi(asana_api_client)
task_api_instance = asana.TasksApi(asana_api_client)
net_gross_different_dict = dict()
def _process_invoice(invoices):
    res = {
        "INV": {
            "Sent": {},
            "Paid": {},
            "Voided": {}
        },
        "BIL": {
            "Draft": [],
            "Awaiting Approval": [],
            "Awaiting Payment": [],
            "Paid": [],
            "Voided": []
        }
    }
    invoices = invoices.to_dict()["invoices"]
    for invoice in invoices:
        if invoice["type"] == "ACCREC":
            if invoice["status"] == "AUTHORISED":
                res["INV"]["Sent"][invoice["invoice_number"]] = {
                        "invoice_number": invoice["invoice_number"],
                        "Payment Date": None if len(invoice["payments"]) == 0 else invoice["payments"][-1]["date"],
                        "Payment Amount": None if float(invoice["amount_paid"]) == 0 else str(invoice["amount_paid"]),
                        "Net": str(invoice["sub_total"]),
                        "Gross": str(invoice["total"]),
                        "reference": invoice["reference"]
                }
            elif invoice["status"] == "PAID":
                res["INV"]["Paid"][invoice["invoice_number"]] = {
                        "Payment Date": None if len(invoice["payments"]) == 0 else invoice["payments"][-1]["date"],
                        "Payment Amount": None if float(invoice["amount_paid"]) == 0 else str(invoice["amount_paid"]),
                        "Net": str(invoice["sub_total"]),
                        "Gross": str(invoice["total"]),
                        "reference": invoice["reference"]
                    }
            elif invoice["status"] == "VOIDED":
                res["INV"]["Voided"][invoice["invoice_number"]] = {
                        "invoice_number": invoice["invoice_number"],
                        "Payment Date": None,
                        "Payment Amount": None,
                        "Net": str(invoice["sub_total"]),
                        "Gross": str(invoice["total"]),
                        "reference": invoice["reference"]
                }
        elif invoice["type"] == "ACCPAY":
            if invoice["status"] == "DRAFT":
                res["BIL"]["Draft"].append(invoice["invoice_number"])
            elif invoice["status"] == "SUBMITTED":
                res["BIL"]["Awaiting Approval"].append(invoice["invoice_number"])
            elif invoice["status"] == "AUTHORISED":
                res["BIL"]["Awaiting Payment"].append(invoice["invoice_number"])
            elif invoice["status"] == "PAID":
                res["BIL"]["Paid"].append(invoice["invoice_number"])
            elif invoice["status"] == "VOIDED":
                res["BIL"]["Voided"].append(invoice["invoice_number"])
        else:
            print("Error, Unsupported Invoice Type")
            return
    return res
@xero_token_required
def update_xero_and_asana_daily_script():
    start = time.time()
    xero_tenant_id = get_xero_tenant_id()
    accounting_api = AccountingApi(api_client)

    invoices = _process_invoice(accounting_api.get_invoices(xero_tenant_id, if_modified_since=if_modified_since))
    for invoices_states in invoices["INV"].keys():
        for invoice_number in invoices["INV"][invoices_states].keys():
            invoice_json[invoice_number] = invoices_states

    for key, value in invoices["BIL"].items():
        for inv_number in value:
            bill_json[inv_number] = key
    off_set = None
    opt_field = ["name", "custom_fields.name", "custom_fields.display_value"]

    custom_fields_setting = clean_response(custom_fields_setting_api_instance.get_custom_field_settings_for_project(invoice_status_asana_gid))
    all_custom_fields = [custom_field["custom_field"] for custom_field in custom_fields_setting]
    custom_field_id_map = name_id_map(all_custom_fields)

    status_field = clean_response(
        custom_fields_api_instance.get_custom_field(custom_field_id_map["Invoice status"]))
    status_id_map = name_id_map(status_field["enum_options"])


    # if generate_management_report:
    #     resource_dir = conf["resource_dir"]
    #     management_report_template = os.path.join(resource_dir, "xlsx", "management_report_template.xlsx")
    #     management_report = os.path.join(conf["database_dir"], f"Management Report {str(date.today().strftime('%Y%m%d'))}.xlsx")
    #     shutil.copy(management_report_template, management_report)
    #     excel = win32client.Dispatch("Excel.Application")
    #     work_book = excel.Workbooks.Open(management_report)
    #     work_sheets = work_book.Worksheets[1]
    #     cur_row = 8
    while True:
        if off_set is None:
            ori_tasks = task_api_instance.get_tasks_for_project(project_gid=invoice_status_asana_gid, opt_fields=opt_field, limit=100).to_dict()
        else:
            ori_tasks = task_api_instance.get_tasks_for_project(project_gid=invoice_status_asana_gid, opt_fields=opt_field, limit=100, offset=off_set).to_dict()
        for task in ori_tasks["data"]:
            print(f"Processing task {task['name'][4:10]}, the gid is {task['gid']}")
            for invoices_states in invoices["INV"].keys():
                if task["name"][4:10] in invoices["INV"][invoices_states].keys():
                    inv = invoices["INV"][invoices_states][task["name"][4:10]]
                    for custom_field in task["custom_fields"]:
                        custom_field_body = {}
                        if custom_field["name"] == "Invoice status" and custom_field["display_value"] != invoices_states:
                            custom_field_body[custom_field_id_map["Invoice status"]] = status_id_map[invoices_states]
                        elif custom_field["name"] == "Payment Date":
                            # if custom_field["display_value"] != inv["Payment Date"]:
                            display_value = None if custom_field["display_value"] is None else custom_field["display_value"][0:10]
                            payment_date = None if inv["Payment Date"] is None else str(inv["Payment Date"])
                            if display_value != payment_date:
                               custom_field_body[custom_field_id_map["Payment Date"]] = {"date":str(inv["Payment Date"])}
                        elif custom_field["name"] == "Payment Amount" and custom_field["display_value"] != inv["Payment Amount"]:
                            custom_field_body[custom_field_id_map["Payment Amount"]] = inv["Payment Amount"]
                        elif custom_field["name"] == "Net" and custom_field["display_value"] != inv["Net"]:
                            if task["name"][4:10] in net_gross_different_dict.keys():
                                net_gross_different_dict[task["name"][4:10]]["Net"]={
                                        "Asana":custom_field["display_value"],
                                        "Xero":inv["Net"]
                                    }
                            else:
                                net_gross_different_dict[task["name"][4:10]] = {
                                    "Net":{
                                        "Asana":custom_field["display_value"],
                                        "Xero":inv["Net"]
                                    }
                                }
                        elif custom_field["name"] == "Gross" and custom_field["display_value"] != inv["Gross"]:
                            if task["name"][4:10] in net_gross_different_dict.keys():
                                net_gross_different_dict[task["name"][4:10]]["Gross"]={
                                        "Asana":custom_field["display_value"],
                                        "Xero":inv["Gross"]
                                    }
                            else:
                                net_gross_different_dict[task["name"][4:10]] = {
                                    "Gross":{
                                        "Asana":custom_field["display_value"],
                                        "Xero":inv["Gross"]
                                    }
                                }
                        if len(custom_field_body) !=0:
                            body = asana.TasksTaskGidBody(
                                {
                                    "custom_fields":custom_field_body
                                }
                            )
                            task_api_instance.update_task(task_gid=task["gid"], body=body)
                            print(f"Process {task['name'][4:10]}")
                        #     custom_field_body[custom_field_id_map["Invoice status"]]: status_id_map[invoices_states]
                    # if generate_management_report:
                    #     work_sheets.Cells(cur_row, 1).Value = inv["reference"]
                    #     work_sheets.Cells(cur_row, 2).Value = task["name"][4:10]

                        # work_sheets.Cells(cur_row, 1).Value = inv["reference"]
                        # work_sheets.Cells(cur_row, 1).Value = inv["reference"]



        if ori_tasks["next_page"] is None:
            with open(os.path.join(database_dir, "invoices.json"), "w") as f:
                json_object = json.dumps(invoice_json, indent=4)
                f.write(json_object)
            with open(os.path.join(database_dir, "bills.json"), "w") as f:
                json_object = json.dumps(bill_json, indent=4)
                f.write(json_object)
            with open(os.path.join(database_dir, "net_gross_different.json"), "w") as f:
                json_object = json.dumps(net_gross_different_dict, indent=4)
                f.write(json_object)
            print(f"The Sync take {time.time() - start}s")
            print("Complete")
            break
        off_set = ori_tasks["next_page"]["offset"]
        # messagebox.showerror("Error", e)



if __name__ == '__main__':
    refresh_token()
    update_xero_and_asana_daily_script()