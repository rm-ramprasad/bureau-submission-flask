There are two parts to this requirement.

1) Build a front end interface in flask that asks users to select a date and a submission type (Consumer or Commercial or NeSL), and a bureau type (Experian, CIBIL, Highmark, Equifax). Once this is submitted the request is to be stored in a DB table

2) Once the request is submitted, then the file generation processes is to be kicked off for that request. For which, the design_plan.md is the plan for consumer submission. Depending on which bureau is in the request, the only difference in the file is hte bureau identifier/customer id for that bureau and rest of hte attribtues are the exact same. 