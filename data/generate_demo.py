"""Reproduce the illustrative vessel workload; no observed annual totals used."""
from pathlib import Path
import csv
import random


def main():
    rng = random.Random(42)
    path = Path(__file__).resolve().parent / 'illustrative_calls.csv'
    fields = ['port', 'vessel_id', 'arrival_hour', 'import_boxes', 'export_boxes',
              'length_m', 'draft_m', 'box40_share', 'evidence_type', 'evidence_reference', 'note']
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for port in ('Guam', 'Conley'):
            arrival = 0.0
            for index in range(24):
                writer.writerow({'port': port, 'vessel_id': f'{port[:1]}-DEMO-{index + 1:03}',
                                 'arrival_hour': round(arrival, 3),
                                 'import_boxes': rng.randint(320, 410),
                                 'export_boxes': rng.randint(160, 220),
                                 'length_m': rng.choice((145, 165, 190, 210)),
                                 'draft_m': rng.choice((7.0, 8.0, 8.5, 9.0)),
                                 'box40_share': .5, 'evidence_type': 'synthetic',
                                 'evidence_reference': 'DEMO-SEED-42',
                                 'note': 'Illustrative operational trace. 40ft share is a sensitivity assumption.'})
                arrival += rng.uniform(9, 11)
    print(path)


if __name__ == '__main__':
    main()
