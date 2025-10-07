#!/bin/bash

# List all folders except numeric folders
options=($(ls -d */ | grep -Ev '^[0-9]+/$' | grep -v 'generated_smp/'))

echo "Select a folder:"
select selected_folder in "${options[@]}"; do
    if [[ -n $selected_folder ]]; then
        # Remove trailing slash
        selected_folder=${selected_folder%/}
        
        # Find highest numbered folder (max zoom)
        max_zoom=$(ls -d [0-9]*/ 2>/dev/null | sort -V | tail -1 | tr -d '/')
        if [[ -z $max_zoom ]]; then
            echo "Error: No numbered folders found"
            exit 1
        fi
        
        # Debug: show detected folders
        echo "Available numbered folders: $(ls -d [0-9]*/ 2>/dev/null | tr -d '/' | tr '\n' ' ')"
        
        # Prepare variables
        current_year=$(date +%Y)
        folder_name="$selected_folder"
        name="javari-${folder_name}-${current_year}-z${max_zoom}"
        tmp_dir="temp_${name}_$(date +%s)"
        output_dir="generated_smp"
        
        echo "Processing folder: $selected_folder"
        echo "Max zoom level: $max_zoom"
        echo "Output name: $name"
        
        # Create temp directory structure
        mkdir -p "$tmp_dir/s/0"
        
        # Copy all numbered folders to s/0
        echo "Copying numbered folders (0-$max_zoom) to s/0..."
        for i in $(seq 0 $max_zoom); do
            if [[ -d "$i" ]]; then
                echo "  Copying folder: $i"
                cp -r "$i" "$tmp_dir/s/0/"
            else
                echo "  Skipping missing folder: $i"
            fi
        done
        
        # Merge selected folder content into s/0
        echo "Merging $selected_folder content into s/0..."
        if [[ -d "$selected_folder" ]]; then
            if ls "$selected_folder"/* > /dev/null 2>&1; then
                cp -r "$selected_folder"/* "$tmp_dir/s/0/" 2>/dev/null || true
                echo "  Content merged successfully"
            else
                echo "  Selected folder is empty"
            fi
        else
            echo "  Warning: Selected folder does not exist"
        fi
        
        # Create style.json with dynamic variables
        echo "Creating style.json..."
        cat > "$tmp_dir/style.json" << EOF
{
  "version": 8,
  "name": "$name",
  "sources": {
    "mbtiles-source": {
      "format": "jpg",
      "name": "$name",
      "version": "2.0",
      "type": "raster",
      "minzoom": 0,
      "maxzoom": $max_zoom,
      "scheme": "xyz",
      "bounds": [-73.740234375, -7.231698708367133, -69.3896484375, -4.346411275333186],
      "center": [0, 0, 6],
      "tiles": ["smp://maps.v1/s/0/{z}/{x}/{y}.jpg"]
    }
  },
  "layers": [
    {
      "id": "background",
      "type": "background",
      "paint": {
        "background-color": "white"
      }
    },
    {
      "id": "raster",
      "type": "raster",
      "source": "mbtiles-source",
      "paint": {
        "raster-opacity": 1
      }
    }
  ],
  "metadata": {
    "smp:bounds": [-73.740234375, -7.231698708367133, -69.3896484375, -4.346411275333186],
    "smp:maxzoom": $max_zoom,
    "smp:sourceFolders": {
      "mbtiles-source": "0"
    }
  },
  "center": [-71.56494140625, -5.7890549918501595],
  "zoom": 11
}
EOF
        
        # Create output directory
        mkdir -p "$output_dir"
        
        # Create the SMP file (zip with .smp extension)
        echo "Creating SMP file..."
        
        # Show what we're about to zip
        echo "Contents in temp directory:"
        find "$tmp_dir" -type f | wc -l
        echo "files total"
        
        # Create zip from inside the temp directory to avoid path issues
        cd "$tmp_dir"
        if zip -r "../${output_dir}/${name}.smp" * > /dev/null 2>&1; then
            echo "ZIP creation successful"
        else
            echo "Error: ZIP creation failed"
            cd - > /dev/null
            rm -rf "$tmp_dir"
            exit 1
        fi
        cd - > /dev/null
        
        # Verify file was created before cleanup
        if [[ -f "${output_dir}/${name}.smp" ]]; then
            echo "Process completed successfully!"
            echo "Output file: ${output_dir}/${name}.smp"
            echo "File size: $(du -h "${output_dir}/${name}.smp" | cut -f1)"
        else
            echo "Error: Output file was not created"
            rm -rf "$tmp_dir"
            exit 1
        fi
        
        # Clean up temporary directory
        rm -rf "$tmp_dir"
        
        break
    else
        echo "Invalid selection."
    fi
done