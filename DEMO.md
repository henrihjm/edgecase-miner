# Two-minute demo

1. **The problem (15 s).** "Robotics and AV teams have hours of footage and no time to label it."
2. **One request (20 s).** Type `forklift passing close to a person`, tick the warehouse group,
   press Mine. Point at the counters: searched, verified, confirmed, rejected.
3. **Confirmed clips (25 s).** Play one. Read its labels (action, distance, lighting) and the
   Cosmos reasoning under it. "The model watched the clip. This is not a caption match."
4. **Rejected strip (20 s).** Show one rejection and its reason. "Search said yes, the model said
   no. That is why the dataset can be trusted."
5. **Coverage and gaps (20 s).** Red cells in the table, then the gap report and collection plan.
   "It also tells you what you do not have."
6. **Export (10 s).** Press Export dataset, open the zip: manifest, labels.csv, clips, dataset card.
7. **The loop (30 s).** Too few clips: press "Propose a better ingestion prompt". Show the prompt
   and the chunks, approve, show before and after counts. "The prompt decides what gets indexed,
   so the agent rewrites the prompt." Run it live only if one iteration measured under three
   minutes; otherwise show the finished loop log from an earlier run.
