# AIBA - Aritifical Intelligence Business Analyst - Current Version

## CEO, Founders, Business Owners favorite Agent
- The agent that can understand business, have access to the data
- answers Business questions, how are things going on in the business, by retriving data. 
- Helps analyse trends, compare revenue, find anomolies



### Current Architecture


### Main Agent

Should able to handle two jobs

1. Understand the business question, ask questions if needed to understand the problem better. 
2. Orchestrater
    - plan - break into tasks - delegate the agents - monitor
    - have access to it's private folder to store the files.


### Sub Agents:

SQL Agent: Able to write sql and get the data along with synthesizer
- Tools: { schema_retriever, sql_validator, execute_sql, fix_sql(max 3 times), synthesizer }

Visulizer agent: if data can visualized it will turn that into visulization. 
- Tools: {python_sandbox}
- Here, based on data we have got, someone should decide whether the answer needs the visulization or not.
- It should be parallel, once the data has retrived, it should pass it to the visualizer

Python_agent: for logical operations of retrieved data
Tools: {python sandbox}

Knowledge Agent: able to answer questions related to company or anything
- Tools: {RAG, web_search}


### Other things to consider
- Once we retreive the data, we should store that data somewhere and shared across the agents so that they can use it for analysis.
